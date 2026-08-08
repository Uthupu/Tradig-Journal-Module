# -*- coding: utf-8 -*-
from psycopg2 import errorcodes

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class GrowDailySummary(models.Model):
    _name = 'grow.daily.summary'
    _description = 'Daily Trading Summary'
    _order = 'date desc'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name', store=True)
    date = fields.Date(string="Date", required=True, index=True,
                        default=fields.Date.context_today)
    user_id = fields.Many2one(
        'res.users', string="Trader", required=True, index=True,
        default=lambda self: self.env.user)
    currency_id = fields.Many2one(
        'res.currency', string="Currency", default=lambda self: self.env.ref(
            'base.INR', raise_if_not_found=False))

    opening_balance = fields.Float(
        string="Opening Balance", digits=(16, 2),
        help="Account balance at the start of the trading day. "
             "Use 'Pull Previous Closing Balance' to auto-fill from the prior day.")
    trade_ids = fields.One2many(
        'grow.intraday.trade', 'daily_summary_id', string="Trades")
    trade_count = fields.Integer(compute='_compute_trade_stats', string="# Trades")
    winning_trades = fields.Integer(compute='_compute_trade_stats', string="Winning Trades")
    losing_trades = fields.Integer(compute='_compute_trade_stats', string="Losing Trades")

    total_daily_profit = fields.Float(
        string="Total Daily Profit", digits=(16, 2), compute='_compute_pl', store=True,
        help="Sum of Profit/Loss for all winning trades on this day.")
    total_daily_loss = fields.Float(
        string="Total Daily Loss", digits=(16, 2), compute='_compute_pl', store=True,
        help="Sum of Profit/Loss for all losing trades on this day (shown as a positive number).")
    net_daily_pl = fields.Float(
        string="Net Daily P/L", digits=(16, 2), compute='_compute_pl', store=True)
    closing_balance = fields.Float(
        string="Closing Balance", digits=(16, 2), compute='_compute_closing_balance', store=True)

    monthly_summary_id = fields.Many2one(
        'grow.monthly.summary', string="Monthly Summary", ondelete='restrict',
        index=True, copy=False, readonly=True,
        help="LOOPHOLE FIX: was 'cascade' — deleting a Monthly Summary used "
             "to silently cascade-delete every Daily Summary (and, in turn, "
             "every Trade) for that month. Now it is protected.")
    company_id = fields.Many2one(
        'res.company', string="Company", default=lambda self: self.env.company)

    _sql_constraints = [
        ('date_user_uniq', 'unique(date, user_id, company_id)',
         'A daily summary for this trader and date already exists.'),
    ]

    @api.constrains('date')
    def _check_date_not_future(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.date and rec.date > today:
                raise ValidationError(_(
                    "Daily Summary date cannot be in the future (%(date)s).",
                    date=rec.date))

    @api.depends('date', 'user_id')
    def _compute_display_name(self):
        for rec in self:
            if rec.date and rec.user_id:
                rec.display_name = f"{rec.user_id.name} - {rec.date}"
            else:
                rec.display_name = _("New Daily Summary")

    @api.depends('trade_ids.profit_loss', 'trade_ids.result')
    def _compute_trade_stats(self):
        for rec in self:
            rec.trade_count = len(rec.trade_ids)
            rec.winning_trades = len(rec.trade_ids.filtered(lambda t: t.result == 'profit'))
            rec.losing_trades = len(rec.trade_ids.filtered(lambda t: t.result == 'loss'))

    @api.depends(
        'trade_ids',
        'trade_ids.profit_loss',
        'trade_ids.result'
    )
    def _compute_pl(self):
        for rec in self:
            profits = sum(t.profit_loss for t in rec.trade_ids if t.profit_loss > 0)
            losses = sum(t.profit_loss for t in rec.trade_ids if t.profit_loss < 0)
            rec.total_daily_profit = profits
            rec.total_daily_loss = abs(losses)
            rec.net_daily_pl = profits + losses

    @api.depends('opening_balance', 'net_daily_pl')
    def _compute_closing_balance(self):
        for rec in self:
            rec.closing_balance = rec.opening_balance + rec.net_daily_pl

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._link_monthly_summary()
        records._recalculate_balances()
        return records

    def unlink(self):
        # LOOPHOLE FIX: clear, translated message instead of a raw
        # DB FK-restrict error when trades are still attached.
        for rec in self:
            if rec.trade_ids:
                raise UserError(_(
                    "Cannot delete '%(name)s': it still has %(count)s "
                    "trade(s) linked to it. Delete or reassign those trades "
                    "first.", name=rec.display_name, count=len(rec.trade_ids)))
        return super().unlink()

    def write(self, vals):
        res = super().write(vals)

        if any(field in vals for field in ('date', 'user_id', 'company_id')):
            self._link_monthly_summary()

        return res


    def _link_monthly_summary(self):
        Monthly = self.env['grow.monthly.summary']

        for rec in self:
            if not rec.date or not rec.user_id:
                continue

            month = f"{rec.date.month:02d}"

            monthly = Monthly.search([
                ('month', '=', month),
                ('year', '=', rec.date.year),
                ('user_id', '=', rec.user_id.id),
                ('company_id', '=', rec.company_id.id),
            ], limit=1)

            if not monthly:
                # LOOPHOLE FIX: same race-safe create-or-fetch as trades ->
                # daily summaries (see intraday_trade.py), applied one level
                # up so two same-month daily summaries created together
                # can't collide on the unique constraint.
                try:
                    with self.env.cr.savepoint():
                        monthly = Monthly.create({
                            'month': month,
                            'year': rec.date.year,
                            'user_id': rec.user_id.id,
                            'company_id': rec.company_id.id,
                            'currency_id': rec.currency_id.id,
                        })
                except Exception as exc:
                    if getattr(exc, 'pgcode', None) == errorcodes.UNIQUE_VIOLATION:
                        monthly = Monthly.search([
                            ('month', '=', month),
                            ('year', '=', rec.date.year),
                            ('user_id', '=', rec.user_id.id),
                            ('company_id', '=', rec.company_id.id),
                        ], limit=1)
                    else:
                        raise

            rec.monthly_summary_id = monthly.id

    def action_pull_previous_closing_balance(self):
        """Auto-fill Opening Balance from the previous trading day's Closing Balance."""
        for rec in self:
            # LOOPHOLE FIX: the previous lookup ignored company_id, so in a
            # multi-company database a trader's opening balance could be
            # silently pulled from another company's books.
            previous = self.search([
                ('user_id', '=', rec.user_id.id),
                ('company_id', '=', rec.company_id.id),
                ('date', '<', rec.date),
            ], order='date desc', limit=1)
            if not previous:
                raise UserError(_(
                    "No earlier daily summary was found for %(user)s to pull a "
                    "closing balance from.", user=rec.user_id.name))
            rec.opening_balance = previous.closing_balance

    def _recalculate_balances(self):
        """Roll opening/closing balances forward, day after day, per trader.

        BUGFIX: this used to UPDATE opening_balance with a raw SQL query and
        then only invalidate the 'opening_balance' cache. That bypasses the
        ORM write path entirely, so the stored 'closing_balance' field (whose
        compute depends on 'opening_balance') was never marked to recompute.
        The result: closing_balance stayed frozen at whatever it was the
        first time it *was* computed through the ORM (in practice, only the
        very first daily summary ever ended up correct), and every KPI that
        reads it downstream - Current Balance, the equity curve, Monthly
        Summary balances - looked stuck/stale on the dashboard.

        BUGFIX 2: the old lookup used self[0].user_id / self[0].company_id
        only, so when this ran for a batch that spanned more than one
        trader or company (e.g. a supervisor editing several traders' trades
        at once, or a multi-company unlink), every trader except the first
        one in the batch was silently skipped.

        Fix: assign via the ORM (triggers the normal compute chain, so
        closing_balance is always correct immediately after this runs), and
        recalculate once per distinct (trader, company) pair present in self.
        """
        if not self:
            return

        seen = set()
        for rec in self:
            key = (rec.user_id.id, rec.company_id.id)
            if key in seen:
                continue
            seen.add(key)

            summaries = self.search([
                ('user_id', '=', key[0]),
                ('company_id', '=', key[1]),
            ], order='date asc')

            previous = 0.0
            for day in summaries:
                if day.opening_balance != previous:
                    day.opening_balance = previous
                previous = day.closing_balance