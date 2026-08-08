# -*- coding: utf-8 -*-
{
    'name': "Trading Journal",
    'summary': "Record intraday trades and analyze daily and monthly trading performance in Odoo 19.",
    'description': """
        Trading Journal for Odoo 19
        ====================================

        A focused trading journal for recording intraday stock trades and analyzing
        daily and monthly performance.

        Key Features
        ------------
        * Long and short trade tracking with automatic Profit/Loss calculation.
        * Automatic Daily and Monthly Summaries.
        * Opening and closing balance roll-forward.
        * Interactive dashboard with KPIs, equity curve, monthly P/L, top symbols,
        streaks and recent trades.
        * Custom date-range filtering.
        * Trader and Trading Supervisor security roles with record-level privacy.
        * Validation for prices, quantities, dates and currency consistency.
        * Safe relationships that protect trading history from accidental cascading deletion.
        * Supervisor recomputation safety tool for imports and restored databases.
        * No external API or third-party service is required.
        * All trading data remains in the customer's Odoo database.
        """,
    'version': '19.0.0.0.1',
    'category': 'Accounting/Accounting',
    'author': "Uthupu Jose",
    'license': 'LGPL-3',
    'price': 39.0,
    'currency': 'EUR',
    'depends': ['base', 'mail', 'web'],
    'data': [
        'security/trading_security.xml',
        'security/ir.model.access.csv',
        'security/trading_record_rules.xml',
        'data/currency_data.xml',
        'views/trade_views.xml',
        'views/daily_summary_views.xml',
        'views/monthly_summary_views.xml',
        'views/dashboard_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'grow_intraday_trading/static/src/scss/dashboard.scss',
            'grow_intraday_trading/static/src/js/dashboard.js',
            'grow_intraday_trading/static/src/xml/dashboard.xml',
        ],
    },
    'images': ['images/intraday_trading_journal_cover.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
