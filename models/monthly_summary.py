# -*- coding: utf-8 -*-
import calendar

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class GrowMonthlySummary(models.Model):
    _name = 'grow.monthly.summary'
    _description = 'Monthly Trading Summary'
    _order = 'year desc, month desc'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name', store=True)
    month = fields.Selection(
        [('01', 'January'), ('02', 'February'), ('03', 'March'), ('04', 'April'),
         ('05', 'May'), ('06', 'June'), ('07', 'July'), ('08', 'August'),
         ('09', 'September'), ('10', 'October'), ('11', 'November'), ('12', 'December')],
        string="Month", required=True,
        help="Keys are zero-padded ('01'-'12') so text-based sorting/grouping "
             "still matches calendar order (otherwise '9' sorts after '12').")
    year = fields.Integer(string="Year", required=True,
                           default=lambda self: fields.Date.context_today(self).year)
    user_id = fields.Many2one(
        'res.users', string="Trader", required=True, index=True,
        default=lambda self: self.env.user)
    currency_id = fields.Many2one(
        'res.currency', string="Currency", default=lambda self: self.env.ref(
            'base.INR', raise_if_not_found=False))

    daily_summary_ids = fields.One2many(
        'grow.daily.summary', 'monthly_summary_id', string="Daily Summaries")
    trading_days = fields.Integer(compute='_compute_totals', string="Trading Days", store=True)

    total_monthly_profit = fields.Float(
        string="Total Monthly Profit", digits=(16, 2), compute='_compute_totals', store=True,
        help="Sum of Total Daily Profit across every day in the month.")
    total_monthly_loss = fields.Float(
        string="Total Monthly Loss", digits=(16, 2), compute='_compute_totals', store=True,
        help="Sum of Total Daily Loss across every day in the month (shown as a positive number).")
    net_monthly_pl = fields.Float(
        string="Net Monthly P/L", digits=(16, 2), compute='_compute_totals', store=True)
    average_daily_pl = fields.Float(
        string="Average Daily P/L", digits=(16, 2), compute='_compute_totals', store=True)

    opening_balance = fields.Float(
        string="Opening Balance", digits=(16, 2), compute='_compute_balances', store=True,
        help="Opening Balance of the first trading day recorded in the month.")
    closing_balance = fields.Float(
        string="Closing Balance", digits=(16, 2), compute='_compute_balances', store=True,
        help="Closing Balance of the last trading day recorded in the month.")
    best_winning_streak = fields.Integer(
        string="Best Winning Streak", compute='_compute_streaks', store=True,
        help="Longest run of consecutive profitable trading days in the month.")
    best_losing_streak = fields.Integer(
        string="Worst Losing Streak", compute='_compute_streaks', store=True,
        help="Longest run of consecutive loss-making trading days in the month.")
    company_id = fields.Many2one(
        'res.company', string="Company", default=lambda self: self.env.company)

    _sql_constraints = [
        ('month_year_user_uniq', 'unique(month, year, user_id, company_id)',
         'A monthly summary for this trader and period already exists.'),
    ]

    @api.constrains('year')
    def _check_year(self):
        current_year = fields.Date.context_today(self).year
        for rec in self:
            if rec.year and (rec.year < 2000 or rec.year > current_year + 1):
                raise ValidationError(_(
                    "Year (%(year)s) looks incorrect.", year=rec.year))

    @api.depends('month', 'year', 'user_id')
    def _compute_display_name(self):
        for rec in self:
            if rec.month and rec.year and rec.user_id:
                month_name = calendar.month_name[int(rec.month)]
                rec.display_name = f"{rec.user_id.name} - {month_name} {rec.year}"
            else:
                rec.display_name = _("New Monthly Summary")

    @api.depends(
        'daily_summary_ids',
        'daily_summary_ids.total_daily_profit',
        'daily_summary_ids.total_daily_loss',
        'daily_summary_ids.net_daily_pl'
    )
    def _compute_totals(self):
        for rec in self:
            days = rec.daily_summary_ids

            rec.trading_days = len(days)

            # Aggregate from each day's already-correct trade-level totals
            # instead of re-filtering by the day's net sign. Netting by day
            # first (old behaviour) silently dropped the loss side of any
            # day that still finished net-positive (and vice versa), so the
            # monthly Profit/Loss figures didn't reconcile with the Daily
            # Summary list.
            rec.total_monthly_profit = sum(days.mapped('total_daily_profit'))
            rec.total_monthly_loss = sum(days.mapped('total_daily_loss'))

            rec.net_monthly_pl = sum(days.mapped('net_daily_pl'))

            rec.average_daily_pl = (
                rec.net_monthly_pl / rec.trading_days
                if rec.trading_days else 0.0
            )

    @api.depends(
        'daily_summary_ids',
        'daily_summary_ids.date',
        'daily_summary_ids.net_daily_pl'
    )
    def _compute_streaks(self):
        for rec in self:

            win = loss = 0
            best_win = best_loss = 0

            days = rec.daily_summary_ids.sorted(
                key=lambda d: (d.date, d.id)
            )

            for day in days:

                if day.net_daily_pl > 0:
                    win += 1
                    loss = 0

                elif day.net_daily_pl < 0:
                    loss += 1
                    win = 0

                else:
                    win = 0
                    loss = 0

                best_win = max(best_win, win)
                best_loss = max(best_loss, loss)

            rec.best_winning_streak = best_win
            rec.best_losing_streak = best_loss

    @api.depends(
        'daily_summary_ids',
        'daily_summary_ids.date',
        'daily_summary_ids.opening_balance',
        'daily_summary_ids.closing_balance'
    )
    def _compute_balances(self):
        for rec in self:
            days = rec.daily_summary_ids.sorted(
                key=lambda d: (d.date, d.id)
            )

            if days:
                rec.opening_balance = days[0].opening_balance
                rec.closing_balance = days[-1].closing_balance
            else:
                rec.opening_balance = 0.0
                rec.closing_balance = 0.0

    def unlink(self):
        # LOOPHOLE FIX: give a clear, translated explanation instead of a
        # raw database FK-restrict error if someone tries to delete a
        # Monthly Summary that still has Daily Summaries under it.
        for rec in self:
            if rec.daily_summary_ids:
                raise UserError(_(
                    "Cannot delete '%(name)s': it still has %(count)s daily "
                    "summary(ies) linked to it. Delete those first, or use "
                    "them if this record was created in error.",
                    name=rec.display_name, count=len(rec.daily_summary_ids)))
        return super().unlink()

    def action_recompute_all(self):
        """Safety-net recompute for all summaries.

        Stored computed fields stay correct automatically as trades are
        entered. This action exists purely as a manual repair tool — e.g.
        after a direct DB import, a restored backup, or any bulk write that
        bypassed the ORM (and therefore bypassed the compute triggers).
        Recomputing here re-derives every total from the trade rows, which
        remain the ultimate source of truth.
        """
        daily = self.env['grow.daily.summary'].search([])
        daily._compute_trade_stats()
        daily._compute_pl()
        daily._compute_closing_balance()

        monthly = self.search([])
        monthly._compute_totals()
        monthly._compute_balances()
        monthly._compute_streaks()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Recompute complete"),
                'message': _(
                    "%(daily)s daily and %(monthly)s monthly summaries "
                    "were refreshed.", daily=len(daily), monthly=len(monthly)),
                'type': 'success',
                'sticky': False,
            },
        }

    @api.model
    def get_dashboard_data(self, period='ytd', date_from=False, date_to=False):
        Daily = self.env['grow.daily.summary']
        Trade = self.env['grow.intraday.trade']

        Daily.flush_model()
        Trade.flush_model()
        self.flush_model()

        self.env.invalidate_all()
        today = fields.Date.context_today(self)

        user = self.env.user
        company = self.env.company

        # Keep whatever the caller passed in (needed for 'custom'); only
        # fall back to the period-based defaults below. Overwriting these
        # unconditionally here was the bug that broke the Custom Date
        # filter: it discarded the date_from/date_to sent by the frontend
        # before the 'custom' branch ever got to read them, so the custom
        # filter always raised "Please select both From Date and To Date."
        custom_date_from = date_from
        custom_date_to = date_to

        date_from = False
        date_to = today

        if period == 'this_month':
            date_from = today.replace(day=1)

        elif period == 'last_3':
            month = today.month - 2
            year = today.year

            while month <= 0:
                month += 12
                year -= 1

            date_from = today.replace(
                year=year,
                month=month,
                day=1
            )

        elif period == 'ytd':
            date_from = today.replace(month=1, day=1)

        elif period == 'custom':
            if not custom_date_from or not custom_date_to:
                raise UserError(_(
                    "Please select both From Date and To Date."
                ))

            date_from = fields.Date.to_date(custom_date_from)
            date_to = fields.Date.to_date(custom_date_to)
            if not date_from or not date_to:
                raise UserError(_(
                    "Invalid custom date range."
                ))
            if date_from > date_to:
                raise UserError(_(
                    "From Date cannot be later than To Date."
                ))

        daily_domain = [
            ('user_id', '=', user.id),
            ('company_id', '=', company.id),
        ]

        if date_from:
            daily_domain.append(('date', '>=', date_from))

        if date_to:
            daily_domain.append(('date', '<=', date_to))

        days = Daily.search(daily_domain, order='date asc')
        latest_day = Daily.search(
            [
                ('user_id', '=', user.id),
                ('company_id', '=', company.id),
                ('date', '>=', date_from) if date_from else ('date', '>=', '1900-01-01'),
                ('date', '<=', date_to),
            ],
            order='date desc',
            limit=1,
        )

        trades = Trade.search([('user_id', '=', user.id),('company_id', '=', company.id),('trade_date', 'in', days.mapped('date')),]) if days else Trade.browse()

        net_pl = sum(days.mapped('net_daily_pl'))
        total_profit = sum(days.mapped('total_daily_profit'))
        total_loss = sum(days.mapped('total_daily_loss'))
        trading_days = len(days)
        winning_days = len(days.filtered(lambda d: d.net_daily_pl > 0))
        win_rate = round((winning_days / trading_days) * 100, 1) if trading_days else 0.0

        current_balance = latest_day.closing_balance if latest_day else 0.0

        best_day = max(days, key=lambda d: d.net_daily_pl) if days else None
        worst_day = min(days, key=lambda d: d.net_daily_pl) if days else None

        # Current streak, walking backward from the most recent trading day.
        sorted_days = days.sorted(key=lambda d: d.date)
        current_streak_count = 0
        current_streak_type = None
        for day in reversed(sorted_days):
            if day.net_daily_pl == 0:
                break
            kind = 'win' if day.net_daily_pl > 0 else 'loss'
            if current_streak_type is None:
                current_streak_type = kind
            if kind != current_streak_type:
                break
            current_streak_count += 1

        equity_curve = [
            {'date': fields.Date.to_string(d.date), 'balance': d.closing_balance, 'pl': d.net_daily_pl}
            for d in sorted_days[-60:]
        ]

        monthly_recs = self.search(
            [
                ('user_id', '=', user.id),
                ('company_id', '=', company.id),
            ],
            order='year asc, month asc',
        )
        month_names = dict(self._fields['month'].selection)
        monthly_bar = [
            {'label': f"{month_names.get(m.month, m.month)[:3]} {m.year}", 'net_pl': m.net_monthly_pl}
            for m in monthly_recs[-12:]
        ]

        symbol_stats = {}
        for t in trades:
            symbol_stats.setdefault(t.stock_symbol, 0.0)
            symbol_stats[t.stock_symbol] += t.profit_loss
        top_symbols = sorted(symbol_stats.items(), key=lambda kv: abs(kv[1]), reverse=True)[:6]

        recent_trades = Trade.search(
            [
                ('user_id', '=', user.id),
                ('company_id', '=', company.id),
            ],
            order='trade_date desc, id desc',
            limit=8,
        )

        return {
            'kpis': {
                'net_pl': net_pl,
                'total_profit': total_profit,
                'total_loss': total_loss,
                'trading_days': trading_days,
                'win_rate': win_rate,
                'current_balance': current_balance,
                'best_day': {'date': fields.Date.to_string(best_day.date), 'pl': best_day.net_daily_pl} if best_day else None,
                'worst_day': {'date': fields.Date.to_string(worst_day.date), 'pl': worst_day.net_daily_pl} if worst_day else None,
                'current_streak': {'type': current_streak_type, 'count': current_streak_count},
            },
            'equity_curve': equity_curve,
            'monthly_bar': monthly_bar,
            'top_symbols': [{'symbol': s, 'pl': pl} for s, pl in top_symbols],
            'recent_trades': [{
                'id': t.id,
                'symbol': t.stock_symbol,
                'date': fields.Date.to_string(t.trade_date),
                'pl': t.profit_loss,
                'result': t.result,
                'state': t.state,
            } for t in recent_trades],
            'currency_symbol': self.env.company.currency_id.symbol or '',
        }
