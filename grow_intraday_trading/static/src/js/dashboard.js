/** @odoo-module **/

import { registry } from "@web/core/registry";
import {
    Component, useState, useRef, onWillStart, onMounted, onPatched, onWillUnmount,
} from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

const PERIODS = [
    { key: "this_month", label: "This Month" },
    { key: "last_3", label: "Last 3 Months" },
    { key: "ytd", label: "Year to Date" },
    { key: "all", label: "All Time" },
];

// New idea: keep the dashboard current on its own instead of only refreshing
// when it happens to be re-opened. Poll on this interval, and also refresh
// immediately whenever the browser tab regains focus/visibility (e.g. the
// trader switches back after logging a trade in another tab).
const AUTO_REFRESH_MS = 60 * 1000;

export class GrowTradingDashboard extends Component {
    static template = "grow_intraday_trading.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.periods = PERIODS;

        this.state = useState({
            loading: true,
            refreshing: false,
            period: "ytd",
            customDateFrom: "",
            customDateTo: "",
            data: null,
            lastUpdated: null,
        });

        this.equityRef = useRef("equityCanvas");
        this.monthlyRef = useRef("monthlyCanvas");
        this._charts = {};
        this._chartJsReady = false;
        // BUGFIX: incremented on every loadData() call and stamped onto the
        // request; a response only gets applied if it's still the most
        // recent request in flight. Previously loadData() was called once
        // in onWillStart AND again in onMounted (and again on every period
        // switch), with no guard - so if an earlier, slower request
        // resolved *after* a later one (e.g. the first page-load request
        // straggling in after the user had already switched periods), it
        // would silently overwrite the newer data with stale values. That's
        // the "dashboard only shows the first entry" symptom: whichever
        // request happened to finish last always won, regardless of which
        // one was actually most recent.
        this._loadSeq = 0;

        onWillStart(async () => {
            await loadJS("/web/static/lib/Chart/Chart.js");
            this._chartJsReady = true;
            await this.loadData();
        });

        // Charts need a live <canvas> element, which only exists after the
        // first render - so render them here once, then keep them in sync
        // via onPatched. Data itself is already loaded by onWillStart;
        // calling loadData() again here just doubles every request.
        onMounted(() => {
            this._renderCharts();
            this._startAutoRefresh();
            document.addEventListener("visibilitychange", this._onVisibilityChange);
        });
        onPatched(() => this._renderCharts());
        onWillUnmount(() => {
            this._stopAutoRefresh();
            document.removeEventListener("visibilitychange", this._onVisibilityChange);
        });

        this._onVisibilityChange = () => {
            if (document.visibilityState === "visible") {
                this.loadData({ silent: true });
            }
        };
    }

    _startAutoRefresh() {
        this._refreshTimer = setInterval(() => {
            this.loadData({ silent: true });
        }, AUTO_REFRESH_MS);
    }

    _stopAutoRefresh() {
        clearInterval(this._refreshTimer);
    }

    /**
     * @param {Object} [opts]
     * @param {boolean} [opts.silent] Don't show the full-screen loading
     *   state - used for background auto-refreshes so the dashboard
     *   doesn't flicker/blank out every 60s or on every tab-focus while
     *   the trader is looking at it. A small "refreshing" indicator is
     *   used instead (see the template).
     */
    async loadData(opts = {}) {
        const { silent = false } = opts;
        if (silent) {
            this.state.refreshing = true;
        } else {
            this.state.loading = true;
        }
        const seq = ++this._loadSeq;
        const period = this.state.period;
        let data;
        try {
            const params = {
                period: this.state.period,
            };

            if (this.state.period === "custom") {
                params.date_from = this.state.customDateFrom;
                params.date_to = this.state.customDateTo;
            }

            data = await this.orm.call(
                "grow.monthly.summary",
                "get_dashboard_data",
                [],
                params
            );
        } finally {
            this.state.refreshing = false;
        }
        if (seq !== this._loadSeq) {
            // A newer request was started while this one was in flight
            // (e.g. rapid period switching, or a manual refresh landing
            // mid-poll) - drop this stale response.
            return;
        }
        // Pre-compute relative bar widths here (not in the template, which
        // has no access to Math) so the symbol list can render simple bars.
        const topSymbols = data.top_symbols || [];
        const maxAbs = Math.max(1, ...topSymbols.map((s) => Math.abs(s.pl)));
        data.top_symbols = topSymbols.map((s) => ({
            ...s,
            width: Math.min(100, (Math.abs(s.pl) / maxAbs) * 100),
        }));
        this.state.data = data;
        this.state.loading = false;
        this.state.lastUpdated = new Date();
    }
    async setCustomDate() {
        this.state.period = "custom";

        if (!this.state.customDateFrom) {
            const today = new Date();

            this.state.customDateFrom = new Date(
                today.getFullYear(),
                today.getMonth(),
                1
            ).toISOString().split("T")[0];
        }

        if (!this.state.customDateTo) {
            this.state.customDateTo = new Date()
                .toISOString()
                .split("T")[0];
        }

        await this.loadData();
    }

    async applyCustomDate() {
        if (!this.state.customDateFrom || !this.state.customDateTo) {
            return;
        }

        if (this.state.customDateFrom > this.state.customDateTo) {
            return;
        }

        await this.loadData();
    }



    async setPeriod(key) {
        if (this.state.period === key) return;
        this.state.period = key;
        await this.loadData();
    }

    async refreshNow() {
        await this.loadData({ silent: true });
    }

    openTrades() {
        this.action.doAction("grow_intraday_trading.action_grow_intraday_trade");
    }

    openMonthlySummaries() {
        this.action.doAction("grow_intraday_trading.action_grow_monthly_summary");
    }

    // ---------- formatting helpers (used from the template) ----------

    fmtMoney(value) {
        const symbol = (this.state.data && this.state.data.currency_symbol) || "";
        const n = Math.round((value || 0) * 100) / 100;
        const sign = n < 0 ? "-" : "";
        const abs = Math.abs(n).toLocaleString("en-IN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
        return `${sign}${symbol}${abs}`;
    }

    fmtCompact(value) {
        const n = value || 0;
        const abs = Math.abs(n);
        if (abs >= 10000000) return (n / 10000000).toFixed(2) + "Cr";
        if (abs >= 100000) return (n / 100000).toFixed(2) + "L";
        if (abs >= 1000) return (n / 1000).toFixed(1) + "k";
        return n.toFixed(0);
    }

    plClass(value) {
        if (!value) return "gtd-flat";
        return value > 0 ? "gtd-pos" : "gtd-neg";
    }

    fmtTime(date) {
        if (!date) return "";
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    // ---------- charts ----------

    _destroyCharts() {
        Object.values(this._charts).forEach((c) => c && c.destroy());
        this._charts = {};
    }

    _renderCharts() {
        if (!this._chartJsReady || !this.state.data) return;
        this._destroyCharts();
        this._renderEquityChart();
        this._renderMonthlyChart();
    }

    _renderEquityChart() {
        const canvas = this.equityRef.el;
        if (!canvas) return;
        const points = this.state.data.equity_curve || [];
        const ctx = canvas.getContext("2d");
        const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height || 220);
        gradient.addColorStop(0, "rgba(56, 224, 189, 0.45)");
        gradient.addColorStop(1, "rgba(56, 224, 189, 0.02)");

        this._charts.equity = new Chart(ctx, {
            type: "line",
            data: {
                labels: points.map((p) => p.date),
                datasets: [{
                    label: "Balance",
                    data: points.map((p) => p.balance),
                    borderColor: "#38e0bd",
                    backgroundColor: gradient,
                    borderWidth: 2.5,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointBackgroundColor: "#38e0bd",
                    tension: 0.35,
                    fill: true,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: "#141b2d",
                        titleColor: "#9fb3d1",
                        bodyColor: "#f2f6fc",
                        borderColor: "rgba(255,255,255,0.08)",
                        borderWidth: 1,
                        padding: 10,
                        displayColors: false,
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { color: "#7d8db3", maxTicksLimit: 6, font: { size: 10 } },
                    },
                    y: {
                        grid: { color: "rgba(255,255,255,0.06)" },
                        ticks: { color: "#7d8db3", font: { size: 10 } },
                    },
                },
            },
        });
    }

    _renderMonthlyChart() {
        const canvas = this.monthlyRef.el;
        if (!canvas) return;
        const bars = this.state.data.monthly_bar || [];
        const ctx = canvas.getContext("2d");

        this._charts.monthly = new Chart(ctx, {
            type: "bar",
            data: {
                labels: bars.map((b) => b.label),
                datasets: [{
                    label: "Net P/L",
                    data: bars.map((b) => b.net_pl),
                    backgroundColor: bars.map((b) =>
                        b.net_pl >= 0 ? "rgba(56, 224, 189, 0.85)" : "rgba(255, 92, 122, 0.85)"
                    ),
                    borderRadius: 6,
                    maxBarThickness: 28,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: "#141b2d",
                        titleColor: "#9fb3d1",
                        bodyColor: "#f2f6fc",
                        borderColor: "rgba(255,255,255,0.08)",
                        borderWidth: 1,
                        padding: 10,
                        displayColors: false,
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { color: "#7d8db3", font: { size: 10 } },
                    },
                    y: {
                        grid: { color: "rgba(255,255,255,0.06)" },
                        ticks: { color: "#7d8db3", font: { size: 10 } },
                    },
                },
            },
        });
    }
}

registry.category("actions").add("grow_trading_dashboard", GrowTradingDashboard);
