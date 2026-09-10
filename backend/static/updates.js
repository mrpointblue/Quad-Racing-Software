(() => {
    const status = document.getElementById("software-update-status");
    const check = document.getElementById("software-update-check");
    const install = document.getElementById("software-update-install");
    const currentBuild = document.getElementById("software-update-current");
    const nextBuild = document.getElementById("software-update-next");
    const channel = document.getElementById("software-update-channel");
    const checked = document.getElementById("software-update-checked");
    const safety = document.getElementById("software-update-safety");
    const backup = document.getElementById("software-update-backup");
    const backupRow = document.getElementById("software-update-backup-row");
    let sending = false;
    let timer;
    function showStatus(message, kind = "diagnostics-note") {
        status.textContent = message;
        status.className = kind;
    }
    function render(data) {
        const failed = Boolean(data.error) || ["error", "recovery"].includes(data.phase);
        const successful = !failed && !data.busy && (data.phase === "success" || (data.phase === "ready" && !data.available));
        // Also handle the old host agent's success message without updating that service.
        const message = data.error || (data.phase === "success" ? "Update erfolgreich installiert." : data.message) || "Status unbekannt.";
        showStatus(message, failed ? "error" : successful ? "success" : "diagnostics-note");
        const current = data.installed?.race;
        const next = data.candidate?.race;
        const label = item => item ? (item.revision?.slice(0, 12) || item.id?.slice(0, 19) || "unbekannt") : "unbekannt";
        currentBuild.textContent = label(current);
        nextBuild.textContent = failed ? "—" : label(next);
        channel.textContent = next?.channel || "—";
        checked.textContent = data.checked_at || "—";
        safety.textContent = data.safety?.reason || "";
        safety.hidden = !safety.textContent;
        backup.textContent = data.backup || "—";
        backupRow.hidden = !data.backup;
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
            showStatus("Rennsoftware momentan nicht erreichbar – Verbindung wird automatisch erneut geprüft.");
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
        showStatus("Aktion wird angefordert …");
        try {
            const response = await fetch(`/api/system/update/${action}`, {
                method: "POST", headers: {"X-Quad-Update": "1"}
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || "Anforderung fehlgeschlagen");
            render(data);
        } catch (error) {
            showStatus(error.message, "error");
        } finally {
            sending = false;
            timer = setTimeout(refresh, 1500);
        }
    }
    check.addEventListener("click", () => request("check"));
    install.addEventListener("click", () => request("install"));
    refresh();
})();
