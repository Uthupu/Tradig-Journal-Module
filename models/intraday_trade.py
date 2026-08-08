# -*- coding: utf-8 -*-
from psycopg2 import errorcodes

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class GrowIntradayTrade(models.Model):
    _name = 'grow.intraday.trade'
    _description = 'Intraday Trade Entry'
    _order = 'trade_date desc, id desc'
    _inherit = ['mail.thread']

    name = fields.Char(
        string="Reference", compute='_compute_name', store=True)
    trade_date = fields.Date(
        string="Date", required=True, default=fields.Date.context_today,
        index=True, tracking=True)
    stock_symbol = fields.Char(
        string="Stock/Symbol", required=True, tracking=True,
        help="NSE/BSE ticker symbol of the traded stock, e.g. RELIANCE, TCS.")
    trade_type = fields.Selection(
        [('long', 'Buy First (Long)'),
         ('short', 'Sell First (Short)')],
        string="Trade Type", required=True, default='long', tracking=True,
        help="Long: bought first then sold. Short: sold first then bought back.")
    buy_price = fields.Float(string="Buy Price", digits=(16, 2), required=True)
    sell_price = fields.Float(string="Sell Price", digits=(16, 2), required=True)
    quantity = fields.Integer(string="Quantity", required=True, default=1)
    profit_loss = fields.Float(
        string="Profit/Loss", digits=(16, 2), compute='_compute_profit_loss',
        store=True, help="(Sell Price - Buy Price) x Quantity. "
                          "For short trades the sign is reversed automatically.")
    result = fields.Selection(
        [('profit', 'Profit'), ('loss', 'Loss'), ('breakeven', 'Breakeven')],
        string="Result", compute='_compute_profit_loss', store=True)
    notes = fields.Text(string="Notes/Observations")
    currency_id = fields.Many2one(
        'res.currency', string="Currency", default=lambda self: self.env.ref(
            'base.INR', raise_if_not_found=False),
        required=True)
    user_id = fields.Many2one(
        'res.users', string="Trader", required=True, tracking=True,
        default=lambda self: self.env.user, index=True)
    daily_summary_id = fields.Many2one(
        'grow.daily.summary', string="Daily Summary", ondelete='restrict',
        index=True, copy=False,
        help="LOOPHOLE FIX: was 'cascade' — deleting a Daily Summary used to "
             "silently wipe out every trade booked on it. Now the summary "
             "cannot be deleted while trades are attached.")
    state = fields.Selection(
        [('draft', 'Draft'), ('confirmed', 'Confirmed')],
        string="Status", default='draft', tracking=True)
    company_id = fields.Many2one(
        'res.company', string="Company", default=lambda self: self.env.company)

    @api.depends('stock_symbol', 'trade_date')
    def _compute_name(self):
        for trade in self:
            if trade.stock_symbol and trade.trade_date:
                trade.name = f"{trade.stock_symbol} - {trade.trade_date}"
            else:
                trade.name = _("New Trade")

    @api.depends('buy_price', 'sell_price', 'quantity', 'trade_type')
    def _compute_profit_loss(self):
        for trade in self:
            gross = (trade.sell_price - trade.buy_price) * trade.quantity
            if trade.trade_type == 'short':
                gross = -gross
            trade.profit_loss = gross
            if gross > 0:
                trade.result = 'profit'
            elif gross < 0:
                trade.result = 'loss'
            else:
                trade.result = 'breakeven'

    @api.constrains('buy_price', 'sell_price', 'quantity')
    def _check_values(self):
        for trade in self:
            if trade.buy_price <= 0 or trade.sell_price <= 0:
                raise ValidationError(_(
                    "Buy Price and Sell Price must be greater than zero."))
            if trade.quantity <= 0:
                raise ValidationError(_("Quantity must be greater than zero."))

    @api.constrains('trade_date')
    def _check_trade_date_not_future(self):
        today = fields.Date.context_today(self)
        for trade in self:
            if trade.trade_date and trade.trade_date > today:
                raise ValidationError(_(
                    "Trade Date cannot be in the future (%(date)s).",
                    date=trade.trade_date))

    @api.constrains('currency_id', 'company_id')
    def _check_currency_consistency(self):
        # LOOPHOLE FIX: Daily/Monthly totals are simple sums (no FX conversion).
        # A trade booked in a currency other than the company currency would
        # silently distort every rollup it feeds into. Block it explicitly
        # instead of producing a wrong-but-plausible-looking total.
        for trade in self:
            if trade.currency_id and trade.company_id and trade.currency_id != trade.company_id.currency_id:
                raise ValidationError(_(
                    "This journal totals trades by simple addition and does not convert "
                    "currencies. '%(symbol)s' is booked in %(trade_cur)s but %(company)s "
                    "trades in %(company_cur)s. Please use %(company_cur)s for this trade.",
                    symbol=trade.stock_symbol or _("this trade"),
                    trade_cur=trade.currency_id.name,
                    company=trade.company_id.name,
                    company_cur=trade.company_id.currency_id.name,
                ))

    @api.model_create_multi
    def create(self, vals_list):
        trades = super().create(vals_list)
        trades._link_daily_summary()
        trades.mapped('daily_summary_id')._recalculate_balances()
        return trades


    def write(self, vals):
        res = super().write(vals)

        if 'trade_date' in vals or 'user_id' in vals or 'company_id' in vals:
            self._link_daily_summary()

        self.mapped('daily_summary_id')._recalculate_balances()
        return res

    def unlink(self):
        summaries = self.mapped('daily_summary_id')
        res = super().unlink()
        summaries._recalculate_balances()
        return res

    def _link_daily_summary(self):
        Summary = self.env['grow.daily.summary']

        for trade in self:
            if not trade.trade_date or not trade.user_id:
                continue

            summary = Summary.search([
                ('date', '=', trade.trade_date),
                ('user_id', '=', trade.user_id.id),
                ('company_id', '=', trade.company_id.id),
            ], limit=1)

            if not summary:
                # LOOPHOLE FIX: two trades for the same trader/day saved at
                # the same instant (e.g. bulk import, two browser tabs) used
                # to both pass this search-miss and both try to create a
                # Daily Summary, one of them crashing on the unique
                # constraint instead of simply attaching to the other's
                # record. A savepoint makes the create-or-fetch atomic.
                try:
                    with self.env.cr.savepoint():
                        summary = Summary.create({
                            'date': trade.trade_date,
                            'user_id': trade.user_id.id,
                            'company_id': trade.company_id.id,
                            'currency_id': trade.currency_id.id,
                        })
                except Exception as exc:
                    if getattr(exc, 'pgcode', None) == errorcodes.UNIQUE_VIOLATION:
                        summary = Summary.search([
                            ('date', '=', trade.trade_date),
                            ('user_id', '=', trade.user_id.id),
                            ('company_id', '=', trade.company_id.id),
                        ], limit=1)
                    else:
                        raise

            if trade.daily_summary_id != summary:
                trade.daily_summary_id = summary.id

    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_reset_to_draft(self):
        self.write({'state': 'draft'})
