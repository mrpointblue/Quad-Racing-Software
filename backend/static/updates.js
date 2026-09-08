(() => {
    const status = document.getElementById("software-update-status");
    const check = document.getElementById("software-update-check");
    const install = document.getElementById("software-update-install");
    const build = document.getElementById("software-update-build");
    const safety = document.getElementById("software-update-safety");
    const backup = document.getElementById("software-update-backup");
    let sending = false;
    let timer;
    function render(data) {
        status.textContent = data.error || data.message || "Status unbekannt.";
        const current = data.installed?.race;
        const next = data.candidate?.race;
        const label = item => item ? (item.revision?.slice(0, 12) || item.id?.slice(0, 19) || "unbekannt") : "unbekannt";
        build.textContent = `Installierter Build: ${label(current)} · Verfügbarer Build: ${label(next)}` +
            (next?.channel ? ` · Kanal: ${next.channel}` : "") +
            (data.checked_at ? ` · Letzte Prüfung: ${data.checked_at}` : "");
        safety.textContent = data.safety?.reason || "";
        backup.textContent = data.backup ? `Letzte Update-Sicherung: ${data.backup}` : "";
        check.disabled = sending || !data.enabled || data.busy || data.phase === "recovery";
        install.disabled = sending || !data.enabled || data.busy || !data.available || data.safety?.safe !== true;
    }
    async function refresh() {
        clearTimeout(timer);
        try {
            const response = await fetch("/api/system/update", {cache: "no-store"});
            if (!response.ok) throw new Error("Status nicht erreichbar");
            render(await response.json());
        } catch (_) {
            status.textContent = "Rennsoftware momentan nicht erreichbar – Verbindung wird automatisch erneut geprüft.";
            check.disabled = install.disabled = true;
        } finally {
            timer = setTimeout(refresh, 5000);
        }
    }
    async function request(action) {
        if (sending) return;
        sending = true;
        clearTimeout(timer);
        check.disabled = install.disabled = true;
        status.textContent = "Aktion wird angefordert …";
        try {
            const response = await fetch(`/api/system/update/${action}`, {
                method: "POST", headers: {"X-Quad-Update": "1"}
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || "Anforderung fehlgeschlagen");
            render(data);
        } catch (error) {
            status.textContent = error.message;
        } finally {
            sending = false;
            timer = setTimeout(refresh, 1500);
        }
    }
    check.addEventListener("click", () => request("check"));
    install.addEventListener("click", () => request("install"));
    refresh();
})();
