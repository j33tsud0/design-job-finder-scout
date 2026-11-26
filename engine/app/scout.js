(() => {
    "use strict";

    const $s = id => document.getElementById(id);
    const panel = $s("scout-panel");
    const chips = $s("scout-queries");
    const status = $s("scout-status");
    const scan = $s("scout-scan");
    const key = "opportunity-scout-v1";

    let queries = [];
    let saved = {};
    let initialized = false;
    let busy = false;

    try {
        saved = JSON.parse(
            localStorage.getItem(key) || "{}"
        );
    } catch (_) {}

    function persist() {
        try {
            localStorage.setItem(
                key,
                JSON.stringify({
                    custom: queries.filter(q => q.custom),
                    disabled: queries.filter(q => !q.enabled)
                        .map(q => q.text)
                })
            );
        } catch (_) {}
    }

    function render() {
        chips.replaceChildren();

        for (const q of queries) {
            const b = document.createElement("button");

            b.type = "button";
            b.textContent = q.text;
            b.className =
                (q.custom ? "custom" : "generated") +
                (q.enabled ? "" : " off");

            b.title =
                "Click to enable or disable; " +
                "right-click to edit or remove; double-click to explore the web";

            b.setAttribute(
                "aria-pressed",
                String(q.enabled)
            );

            b.onclick = () => {
                q.enabled = !q.enabled;
                persist();
                render();
            };

            b.ondblclick = () => {
                exploreWeb(q.text);
            };

            b.oncontextmenu = e => {
                e.preventDefault();

                const changed = window.prompt(
                    "Edit query; leave empty to remove",
                    q.text
                );

                if (changed === null) return;

                const value = changed.trim();

                if (!value) {
                    if (q.custom) {
                        queries = queries.filter(x => x !== q);
                    } else {
                        q.enabled = false;
                    }
                } else if (
                    new TextEncoder().encode(value).length <= 512 &&
                    !queries.some(x => x !== q && x.text === value)
                ) {
                    q.text = value;
                    q.custom = true;
                }

                persist();
                render();
            };

            chips.append(b);
        }
    }

    async function refresh() {
        const data = await api("/api/scout/state");

        const disabled = new Set(
            initialized
                ? queries.filter(q => !q.enabled).map(q => q.text)
                : saved.disabled || []
        );

        const custom = initialized
            ? queries.filter(q => q.custom)
            : (saved.custom || []);

        queries = data.queries
            .filter(text => !custom.some(q => q.text === text))
            .map(text => ({
                text,
                custom: false,
                enabled: !disabled.has(text)
            }))
            .concat(custom);

        initialized = true;

        status.textContent = data.configured
            ? "Ready. No API key required. Scan searches public remote jobs."
            : "Public discovery is currently unavailable.";

        render();
    }

    function exploreWeb(query) {
        const url = new URL("https://www.google.com/search");
        url.searchParams.set("q", query);

        window.open(
            url.href,
            "_blank",
            "noopener,noreferrer"
        );
    }

    $s("scout-explore").onclick = () => {
        const active = queries.filter(q => q.enabled);

        if (!active.length) {
            status.textContent = "Enable a query first.";
            return;
        }

        exploreWeb(active[0].text);
    };

    function expand(open) {
        panel.hidden = !open;

        $s("scout-toggle").textContent =
            open ? "Collapse ▼" : "Expand ▲";

        $s("scout-toggle").setAttribute(
            "aria-expanded",
            String(open)
        );
    }

    $s("scout-toggle").onclick = () => {
        expand(panel.hidden);
    };

    $s("scout-add").onclick = () => {
        expand(true);
        $s("scout-input").focus();
    };

    $s("scout-refresh").onclick = async () => {
        try {
            await refresh();
        } catch (e) {
            status.textContent = e.message;
        }
    };

    $s("scout-form").onsubmit = e => {
        e.preventDefault();

        const input = $s("scout-input");
        const text = input.value.trim();

        if (!text ||
            new TextEncoder().encode(text).length > 512 ||
            queries.some(q => q.text === text)) {
            return;
        }

        queries.push({
            text,
            custom: true,
            enabled: true
        });

        input.value = "";
        persist();
        render();
    };

    function showResults(data) {
        const list = $s("scout-results");
        list.replaceChildren();

        for (const result of data.results) {
            let url;

            try {
                url = new URL(result.url);
            } catch (_) {
                continue;
            }

            if (!["https:", "http:"].includes(url.protocol))
                continue;

            const card = document.createElement("article");
            card.className = "scout-result";

            const link = document.createElement("a");
            link.href = url.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = result.title || url.hostname;

            const description = document.createElement("p");
            description.textContent = result.description || "";

            const meta = document.createElement("div");
            meta.className = "scout-meta";

            meta.textContent =
                "Public job listing • Remotive • " +
                url.hostname +
                (result.age ? " • " + result.age : "") +
                " • Matched queries: " +
                result.queries.join(" | ");

            card.append(link, description, meta);
            list.append(card);
        }

        status.textContent =
            `${data.results.length} unique discoveries. ` +
            `${data.failures.length} queries failed. ` +
            "Results are unverified hiring leads.";

        for (const failure of data.failures) {
            const p = document.createElement("p");
            p.textContent =
                failure.query + ": " + failure.error;
            list.append(p);
        }
    }

    scan.onclick = async () => {
        if (busy) return;

        expand(true);

        const active = queries.filter(q => q.enabled)
            .map(q => q.text);

        if (!active.length || active.length > 12) {
            status.textContent =
                "Enable between 1 and 12 queries.";
            return;
        }

        busy = true;
        scan.disabled = true;
        status.textContent =
            "Searching public remote jobs using the first enabled query…";

        try {
            const data = await api(
                "/api/scout/scan",
                {queries: active}
            );

            showResults(data);
        } catch (e) {
            status.textContent = e.message;
        } finally {
            busy = false;
            scan.disabled = false;
        }
    };

    function syncVisibility() {
        const visible = !$s("app").hidden;

        $s("scout").hidden = !visible;

        document.body.classList.toggle(
            "scout-visible",
            visible
        );

        if (visible && !initialized) {
            refresh().catch(e => {
                status.textContent = e.message;
            });
        }
    }

    new MutationObserver(syncVisibility).observe(
        $s("app"),
        {
            attributes: true,
            attributeFilter: ["hidden"]
        }
    );

    syncVisibility();
})();
