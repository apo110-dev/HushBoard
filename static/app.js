(() => {
  "use strict";

  const API_ROOT = "/api";
  const STORAGE_KEY = "hushboard.currentSubmissionId";
  const POLL_INTERVAL = 4000;
  const TERMINAL_STATES = new Set(["refunded", "kept", "mismatch", "failure"]);
  const REVIEW_STATES = new Set(["moderation"]);
  const CLOSED_STATES = new Set(["refunded", "kept", "mismatch", "failure"]);

  const state = {
    health: null,
    submissions: [],
    current: null,
    selected: null,
    selectedId: null,
    filter: "all",
    pollTimer: null,
    pollCount: 0,
    qrSource: null,
    loadingList: true,
    snapshotReadOnly: false
  };

  const el = {};

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    cacheElements();
    bindEvents();
    setWorkspaceView(window.location.hash === "#console" ? "operator" : "participant", false);
    updateCharacterCount();
    restoreCurrentSubmission();
    renderTimeline(null);
    refreshAll({ initial: true });
    window.setInterval(() => {
      request("/health")
        .then((data) => {
          state.health = data || {};
          renderHealth();
        })
        .catch(() => null);
    }, 10000);
  }

  function cacheElements() {
    [
      "network-pill", "network-pill-label", "open-reset", "view-toggle", "feedback-form",
      "feedback-message", "character-count", "refund-address", "form-error",
      "create-button", "composer-view", "invoice-view", "invoice-status-badge",
      "invoice-code", "invoice-amount", "invoice-instruction", "payment-address",
      "copy-address", "pay-button", "wallet-caption", "check-payment", "qr-frame", "qr-placeholder",
      "qr-image", "status-timeline", "outcome-card", "invoice-error", "new-feedback",
      "sync-button", "submission-list", "moderation-detail", "detail-heading",
      "detail-status", "detail-message", "detail-meta", "moderation-actions",
      "refund-button", "keep-button", "detail-error", "empty-state", "count-all",
      "count-review", "count-closed", "health-dot", "health-network", "health-wallet",
      "health-height", "refresh-all", "toast-stack", "policy-dialog", "reset-dialog", "moderator-mode-label",
      "confirm-reset", "reset-error"
    ].forEach((id) => {
      el[toCamel(id)] = document.getElementById(id);
    });
    el.filterButtons = Array.from(document.querySelectorAll("[data-filter]"));
    el.policyTriggers = Array.from(document.querySelectorAll("[data-open-policy]"));
  }

  function bindEvents() {
    el.viewToggle.addEventListener("click", () => {
      setWorkspaceView(document.body.dataset.view === "operator" ? "participant" : "operator");
    });
    el.feedbackMessage.addEventListener("input", updateCharacterCount);
    el.feedbackMessage.addEventListener("blur", () => validateField(el.feedbackMessage));
    el.refundAddress.addEventListener("blur", () => validateField(el.refundAddress));
    el.feedbackForm.addEventListener("submit", createSubmission);
    el.newFeedback.addEventListener("click", showComposer);
    el.copyAddress.addEventListener("click", copyPaymentAddress);
    el.payButton.addEventListener("click", payWithTestWallet);
    el.checkPayment.addEventListener("click", () => loadCurrentSubmission({ announce: true }));
    el.syncButton.addEventListener("click", syncNetwork);
    el.refreshAll.addEventListener("click", () => refreshAll({ announce: true }));
    el.refundButton.addEventListener("click", () => moderate("refund"));
    el.keepButton.addEventListener("click", () => moderate("keep"));

    el.filterButtons.forEach((button) => {
      button.addEventListener("click", () => {
        state.filter = button.dataset.filter || "all";
        el.filterButtons.forEach((item) => {
          const active = item === button;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", String(active));
        });
        renderSubmissionList();
      });
    });

    el.policyTriggers.forEach((button) => button.addEventListener("click", openPolicyDialog));
    el.openReset.addEventListener("click", () => openDialog(el.resetDialog));
    el.confirmReset.addEventListener("click", resetDemo);

    [el.policyDialog, el.resetDialog].forEach((dialog) => {
      dialog.addEventListener("click", (event) => closeOnBackdrop(event, dialog));
    });

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stopPolling();
      else if (state.current && !TERMINAL_STATES.has(state.current.status)) startPolling();
    });
  }

  function setWorkspaceView(view, updateLocation = true) {
    const operator = view === "operator";
    document.body.dataset.view = operator ? "operator" : "participant";
    el.viewToggle.setAttribute("aria-pressed", String(operator));
    const compact = window.matchMedia("(max-width: 620px)").matches;
    el.viewToggle.textContent = operator
      ? compact ? "← Mesaj" : "← Mesaj ekranı"
      : compact ? "Panel →" : "Operatör paneli →";
    if (updateLocation) {
      const base = `${window.location.pathname}${window.location.search}`;
      window.history.replaceState(null, "", operator ? `${base}#console` : base);
    }
  }

  async function request(path, options = {}) {
    const method = options.method || "GET";
    const config = {
      method,
      headers: {
        Accept: "application/json",
        ...(options.headers || {})
      },
      cache: "no-store"
    };

    if (options.body !== undefined) {
      config.headers["Content-Type"] = "application/json";
      config.body = JSON.stringify(options.body);
    }
    if (options.signal) config.signal = options.signal;

    let response;
    try {
      response = await fetch(`${API_ROOT}${path}`, config);
    } catch (error) {
      throw new ApiError("Sunucuya ulaşılamadı. Bağlantıyı kontrol edip yeniden dene.", 0, null, error);
    }

    const raw = await response.text();
    let data = null;
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch (_error) {
        const contentType = response.headers.get("content-type") || "";
        const looksLikePlainText = contentType.includes("text/plain") && !/[<>]/.test(raw);
        data = looksLikePlainText ? { message: raw.trim().slice(0, 180) } : { non_json_response: true };
      }
    }

    if (!response.ok) {
      throw new ApiError(apiErrorMessage(response.status, data), response.status, data);
    }
    return data;
  }

  class ApiError extends Error {
    constructor(message, status, data, cause) {
      super(message, { cause });
      this.name = "ApiError";
      this.status = status;
      this.data = data;
    }
  }

  function apiErrorMessage(status, data) {
    const detail = data && data.detail;
    let serverMessage = "";

    if (Array.isArray(detail)) {
      serverMessage = detail.map((item) => {
        const path = Array.isArray(item.loc) ? item.loc : (Array.isArray(item.location) ? item.location : []);
        const location = path.length ? path[path.length - 1] : "alan";
        return `${location}: ${item.msg || item.message || "geçersiz"}`;
      }).join(" · ");
    } else if (typeof detail === "string") {
      serverMessage = detail;
    } else if (typeof data?.error === "string") {
      serverMessage = data.error;
    } else if (typeof data?.message === "string") {
      serverMessage = data.message;
    }

    if (serverMessage) return serverMessage;
    if (status === 401 || status === 403) return "Bu moderatör işlemi için sunucu yetkisi gerekli.";
    if (status === 404) return "İstenen bildirim bulunamadı; demo sıfırlanmış olabilir.";
    if (status === 409) return "Bu işlem mevcut durumda yapılamıyor. Önce zincir durumunu yenile.";
    if (status === 422) return "Alanlardan biri geçersiz. Bilgileri kontrol edip yeniden dene.";
    if (status === 429) return "Çok hızlı işlem yapıldı. Birkaç saniye sonra yeniden dene.";
    if (status === 503) return "Testnet cüzdanı şu an hazır değil. Ağ durumunu kontrol et.";
    if (status >= 500) return "Sunucuda geçici bir sorun oluştu. İşlem güvenle yeniden denenebilir.";
    return "İstek tamamlanamadı. Lütfen yeniden dene.";
  }

  async function refreshAll({ initial = false, announce = false } = {}) {
    toggleSpin(el.refreshAll, true);
    const tasks = [loadHealth(), loadSubmissions()];
    if (state.current?.id) tasks.push(loadCurrentSubmission({ silent: true }));
    const results = await Promise.allSettled(tasks);
    toggleSpin(el.refreshAll, false);

    if (announce) {
      const failed = results.filter((result) => result.status === "rejected").length;
      showToast(failed ? "Bazı veriler yenilenemedi; mevcut ekran korundu." : "Ağ ve bildirimler yenilendi.", failed ? "error" : "success");
    }
    if (initial && state.current?.id) showInvoice();
  }

  async function loadHealth() {
    setHealthLoading();
    try {
      const data = await request("/health");
      state.health = data || {};
      renderHealth();
      return data;
    } catch (error) {
      renderHealthError(error);
      throw error;
    }
  }

  function setHealthLoading() {
    el.networkPill.className = "network-pill is-loading";
    el.networkPillLabel.textContent = "TESTNET · KONTROL EDİLİYOR";
    el.healthDot.className = "console-dot is-loading";
    el.healthNetwork.textContent = "Kontrol ediliyor";
  }

  function renderHealth() {
    const health = state.health || {};
    const ok = health.ok !== false && health.database?.ok !== false;
    const mode = String(health.mode || "unknown").toLowerCase();
    const network = String(health.network || "testnet").toUpperCase();
    const wallet = health.wallet || {};
    const snapshot = health.snapshot && typeof health.snapshot === "object" ? health.snapshot : null;
    const isLive = mode === "live";
    const isMock = mode === "mock" || health.demo === true;
    const operatorConnected = Boolean(wallet.operator_connected ?? wallet.operatorConnected);
    const participantConnected = Boolean(wallet.participant_connected ?? wallet.participantConnected);
    const connectedCount = Number(operatorConnected) + Number(participantConnected);
    const transientWalletSync = isLive
      && health.database?.ok !== false
      && operatorConnected
      && participantConnected
      && wallet.synced === false;
    const snapshotModeChanged = state.snapshotReadOnly !== Boolean(snapshot);
    state.snapshotReadOnly = Boolean(snapshot);
    [el.feedbackMessage, el.refundAddress, el.createButton, el.newFeedback].forEach((control) => {
      if (control) control.disabled = state.snapshotReadOnly;
    });
    if (!el.createButton.hasAttribute("aria-busy")) {
      setButtonLabel(el.createButton, state.snapshotReadOnly ? "Salt okunur kayıt" : "Ödeme isteği oluştur");
    }

    if (isLive) {
      el.walletCaption.textContent = "Bu düğme yerel test cüzdanından 0,01 TAZ gönderir. TAZ'ın maddi değeri yok.";
    } else if (isMock) {
      el.walletCaption.textContent = snapshot
        ? `Salt okunur kayıt · ${snapshot.captured_at || "zaman bilinmiyor"} · cüzdan gönderimi kapalı.`
        : "Demo modunda testnet gönderimi yapılmaz; ekrandaki akış sentetiktir.";
    } else {
      el.walletCaption.textContent = "Testnet bağlantısı kontrol ediliyor…";
    }

    el.openReset.disabled = isLive || Boolean(snapshot);
    el.openReset.title = isLive
      ? "Canlı testnet kayıtları arayüzden sıfırlanmaz"
      : snapshot ? "Salt okunur kayıt yeniden başlatmada geri yüklenir" : "Demo verisini sıfırla";

    if (
      !el.payButton.hasAttribute("aria-busy")
      && (!state.current || state.current.status === "awaiting_bond")
    ) {
      setButtonLabel(
        el.payButton,
        snapshot ? "Salt okunur kayıt · gönderim kapalı" : isMock ? "Simülasyon: ödeme olayını çalıştır" : "Test cüzdanıyla 0,01 TAZ gönder"
      );
    }
    const refundSmall = el.refundButton.querySelector("small");
    const keepSmall = el.keepButton.querySelector("small");
    if (refundSmall) refundSmall.textContent = snapshot
      ? "Salt okunur · iade kapalı"
      : isMock ? "Simülasyon: iade olayını çalıştır" : "0,01 TAZ iade et";
    if (keepSmall) keepSmall.textContent = snapshot
      ? "Salt okunur · karar kapalı"
      : isMock ? "Simülasyon: tutma kararı" : "teminatı tut";

    el.networkPill.className = `network-pill${ok ? "" : transientWalletSync ? " is-loading" : " is-error"}`;
    if (!ok && transientWalletSync) {
      el.networkPillLabel.textContent = window.matchMedia("(max-width: 520px)").matches
        ? "WALLET · EŞİTLENİYOR"
        : `${network} · WALLET EŞİTLENİYOR`;
      el.networkPill.title = "Düğüm ilerledi; iki cüzdan aynı yüksekliğe yetişiyor. Canlı işlemler hazır olana kadar bekler.";
    } else if (!ok) {
      el.networkPillLabel.textContent = `${network} · SERVİS SORUNU`;
    } else if (isLive) {
      const fullLabel = `GERÇEK ${network} · CANLI`;
      el.networkPillLabel.textContent = window.matchMedia("(max-width: 520px)").matches
        ? `${network} · CANLI`
        : fullLabel;
      el.networkPill.title = fullLabel;
    } else if (isMock) {
      el.networkPill.classList.add("is-loading");
      const fullLabel = snapshot
        ? "OFFLINE REPLAY · NO LIVE SENDS"
        : "DEMO SIMULATION · NO LIVE SENDS";
      el.networkPillLabel.textContent = window.matchMedia("(max-width: 520px)").matches
        ? snapshot ? "OFFLINE REPLAY" : "DEMO SİMÜLASYONU"
        : fullLabel;
      el.networkPill.title = snapshot
        ? `${fullLabel} · kayıt ${snapshot.captured_at || "?"} · blok ${snapshot.block_height || "?"}`
        : `${fullLabel} · sentetik demo modu; zincir kanıtı değildir`;
    } else {
      el.networkPillLabel.textContent = `${network} · BAĞLI`;
    }

    if (el.moderatorModeLabel) {
      clearNode(el.moderatorModeLabel);
      const modeDot = document.createElement("i");
      modeDot.setAttribute("aria-hidden", "true");
      const modeText = snapshot ? " OFFLINE REPLAY" : isMock ? " DEMO SIMULATION" : " CANLI";
      el.moderatorModeLabel.append(modeDot, document.createTextNode(modeText));
      el.moderatorModeLabel.classList.toggle("is-offline", isMock);
    }

    el.healthDot.className = `console-dot${ok ? "" : transientWalletSync ? " is-loading" : " is-error"}`;
    const snapshotClock = snapshot?.captured_at
      ? String(snapshot.captured_at).replace(/^.*T/, "").replace(/:\d{2}Z$/, "Z")
      : null;
    el.healthNetwork.textContent = transientWalletSync
      ? `${network} / eşitleniyor`
      : isLive
        ? `${network} / canlı`
        : isMock && snapshotClock
        ? `REPLAY · ${snapshotClock}`
        : isMock ? `${network} / mock` : network;

    if (isMock && snapshot) el.healthWallet.textContent = "send kapalı · snapshot";
    else if (wallet.synced && connectedCount === 2) el.healthWallet.textContent = "2/2 bağlı · senkron";
    else if (connectedCount) el.healthWallet.textContent = `${connectedCount}/2 bağlı${wallet.synced === false ? " · eşitleniyor" : ""}`;
    else if (isMock) el.healthWallet.textContent = "simüle ediliyor";
    else el.healthWallet.textContent = "bağlantı yok";

    const height = firstDefined(
      health.height,
      health.block_height,
      health.chain_height,
      health.chain?.height,
      health.wallet?.height
    );
    if (height !== undefined && height !== null) {
      el.healthHeight.textContent = formatNumber(height);
    } else if (health.watcher?.last_sync_at) {
      el.healthHeight.textContent = `eşitlendi ${relativeTime(health.watcher.last_sync_at)}`;
    } else {
      el.healthHeight.textContent = wallet.synced ? "senkron" : "—";
    }

    if (snapshotModeChanged && state.submissions.length) {
      renderSubmissionList();
      renderModerationDetail();
    }
  }

  function renderHealthError(error) {
    el.networkPill.className = "network-pill is-error";
    el.networkPillLabel.textContent = "TESTNET · ULAŞILAMIYOR";
    el.healthDot.className = "console-dot is-error";
    el.healthNetwork.textContent = "çevrimdışı";
    el.healthWallet.textContent = "—";
    el.healthHeight.textContent = "—";
    el.networkPill.title = error?.message || "Sağlık bilgisi alınamadı";
  }

  async function loadSubmissions() {
    state.loadingList = true;
    el.submissionList.setAttribute("aria-busy", "true");
    if (!state.submissions.length) renderListSkeleton();

    try {
      const data = await request("/submissions?limit=50&offset=0");
      const items = Array.isArray(data) ? data : (data?.items || data?.submissions || []);
      state.submissions = items.map(normalizeSubmission).filter((item) => item.id);
      state.loadingList = false;
      el.submissionList.setAttribute("aria-busy", "false");

      if (state.selectedId) {
        const updated = state.submissions.find((item) => item.id === state.selectedId);
        if (updated) state.selected = mergeSubmission(state.selected, updated);
      }
      if (!state.selected && state.submissions.length) {
        state.selected = state.submissions.find((item) => REVIEW_STATES.has(item.status)) || state.submissions[0];
        state.selectedId = state.selected.id;
      }
      if (state.selected && !state.submissions.some((item) => item.id === state.selected.id)) {
        state.selected = state.submissions[0] || null;
        state.selectedId = state.selected?.id || null;
      }

      renderSubmissionList();
      renderModerationDetail();
      return state.submissions;
    } catch (error) {
      state.loadingList = false;
      el.submissionList.setAttribute("aria-busy", "false");
      renderListError(error);
      throw error;
    }
  }

  function renderListSkeleton() {
    clearNode(el.submissionList);
    for (let i = 0; i < 2; i += 1) {
      const row = document.createElement("div");
      row.className = "list-skeleton";
      row.setAttribute("aria-hidden", "true");
      row.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
      el.submissionList.append(row);
    }
  }

  function renderListError(error) {
    clearNode(el.submissionList);
    const box = document.createElement("div");
    box.className = "list-error";
    const strong = document.createElement("strong");
    strong.textContent = "Pano yüklenemedi";
    const message = document.createElement("span");
    message.textContent = error?.message || "Sunucuya ulaşılamadı.";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "Yeniden dene";
    retry.addEventListener("click", loadSubmissions);
    box.append(strong, message, retry);
    el.submissionList.append(box);
    updateCounts();
  }

  function renderSubmissionList() {
    updateCounts();
    const items = filteredSubmissions();
    const focusedRowId = document.activeElement?.classList?.contains("submission-row")
      ? document.activeElement.dataset.id
      : null;
    clearNode(el.submissionList);

    if (!state.submissions.length) {
      el.emptyState.hidden = false;
      el.moderationDetail.hidden = true;
      el.submissionList.hidden = true;
      return;
    }

    el.emptyState.hidden = true;
    el.submissionList.hidden = false;

    if (!items.length) {
      const noResult = document.createElement("div");
      noResult.className = "filter-empty";
      noResult.textContent = "Bu filtrede bildirim yok.";
      el.submissionList.append(noResult);
      return;
    }

    items.forEach((submission) => {
      const row = document.createElement("button");
      row.type = "button";
      const isSelected = submission.id === state.selectedId;
      row.className = `submission-row${isSelected ? " is-selected" : ""}`;
      row.dataset.id = submission.id;
      row.setAttribute("aria-pressed", String(isSelected));
      if (isSelected) row.setAttribute("aria-current", "true");
      const replayPrefix = state.snapshotReadOnly ? "offline kayıt, " : "";
      row.setAttribute("aria-label", `${displayCode(submission.id)}, ${replayPrefix}${statusInfo(submission).label}, ${submission.title || submission.body}`);

      const content = document.createElement("span");
      content.className = "row-content";
      const titleLine = document.createElement("span");
      titleLine.className = "row-titleline";
      const id = document.createElement("b");
      id.textContent = displayCode(submission.id);
      const time = document.createElement("time");
      time.dateTime = submission.createdAt || "";
      time.textContent = relativeTime(submission.createdAt);
      titleLine.append(id, time);
      const excerpt = document.createElement("p");
      const evidence = evidenceKindLabel(submission.evidenceKind, true);
      const summary = submission.body || submission.title || "İçerik yok";
      excerpt.textContent = evidence ? `${evidence} · ${summary}` : summary;
      content.append(titleLine, excerpt);

      row.append(content, createStatusBadge(submission));
      row.addEventListener("click", () => selectSubmission(submission.id));
      el.submissionList.append(row);
    });

    if (focusedRowId) {
      const refreshed = Array.from(el.submissionList.querySelectorAll(".submission-row"))
        .find((row) => row.dataset.id === focusedRowId);
      refreshed?.focus({ preventScroll: true });
    }
  }

  function filteredSubmissions() {
    if (state.filter === "review") return state.submissions.filter((item) => REVIEW_STATES.has(item.status));
    if (state.filter === "closed") return state.submissions.filter((item) => CLOSED_STATES.has(item.status));
    return state.submissions;
  }

  function updateCounts() {
    el.countAll.textContent = String(state.submissions.length);
    el.countReview.textContent = String(state.submissions.filter((item) => REVIEW_STATES.has(item.status)).length);
    el.countClosed.textContent = String(state.submissions.filter((item) => CLOSED_STATES.has(item.status)).length);
  }

  async function selectSubmission(id) {
    const inList = state.submissions.find((item) => item.id === id);
    if (inList) {
      state.selected = inList;
      state.selectedId = id;
      renderSubmissionList();
      renderModerationDetail();
    }

    try {
      const data = await request(`/submissions/${encodeURIComponent(id)}`);
      state.selected = normalizeSubmission(data);
      state.selectedId = state.selected.id;
      replaceInList(state.selected);
      renderSubmissionList();
      renderModerationDetail();
    } catch (error) {
      showDetailError(error.message);
    }
  }

  function renderModerationDetail() {
    const submission = state.selected;
    if (!submission || !state.submissions.length) {
      el.moderationDetail.hidden = true;
      return;
    }

    el.moderationDetail.hidden = false;
    el.detailHeading.textContent = displayCode(submission.id);
    el.detailMessage.textContent = submission.body || submission.title || "İçerik yok";
    applyBadge(el.detailStatus, submission);
    renderDetailMeta(submission);

    const canModerate = !state.snapshotReadOnly
      && submission.status === "moderation"
      && !submission.moderation?.decision
      && submission.canModerate !== false;
    el.refundButton.disabled = !canModerate;
    el.keepButton.disabled = !canModerate;
    el.refundButton.title = canModerate ? "Meşru bildirimi iade et" : moderationDisabledReason(submission.status);
    el.keepButton.title = canModerate ? "Spam teminatını operatör cüzdanında tut" : moderationDisabledReason(submission.status);
    el.moderationActions.classList.toggle("is-complete", CLOSED_STATES.has(submission.status) || submission.status === "refund_broadcast");
    hideError(el.detailError);
  }

  function renderDetailMeta(submission) {
    clearNode(el.detailMeta);
    const confirmations = submission.bond.confirmations;
    const required = submission.bond.requiredConfirmations;
    const conf = document.createElement("span");
    conf.textContent = `Bond ${confirmations}/${required || "?"} onay`;
    el.detailMeta.append(conf);

    if (submission.refundAddressHint) {
      const hint = document.createElement("span");
      hint.textContent = "İade adresi maskeli";
      el.detailMeta.append(hint);
    }

    const bondLink = transactionLink(submission.bond.txid, submission.bond.explorerUrl, "Bond işlemi ↗", !submission.demo);
    if (bondLink) el.detailMeta.append(bondLink);
    const refundLink = transactionLink(submission.refund.txid, submission.refund.explorerUrl, "İade işlemi ↗", !submission.demo);
    if (refundLink) el.detailMeta.append(refundLink);

    if (submission.demo) {
      const demo = document.createElement("span");
      demo.textContent = evidenceKindLabel(submission.evidenceKind, false) || "SENTETİK DEMO KAYDI";
      el.detailMeta.append(demo);
    }
  }

  function evidenceKindLabel(kind, short) {
    const captured = state.snapshotReadOnly;
    const labels = captured ? {
      real_confirmed_bond: short ? "KAYIT · GERÇEK TEMİNAT" : "KAYITLI CÜZDAN KANITI · ONAYLI TEMİNAT",
      real_confirmed_refund_e2e: short ? "KAYIT · GERÇEK İADE" : "KAYITLI CÜZDAN KANITI · ONAYLI İADE",
      synthetic_walkthrough: short ? "KAYIT · SENTETİK" : "KAYITLI VERİ · SENTETİK ÖRNEK AKIŞ"
    } : {
      real_confirmed_bond: short ? "GERÇEK TEMİNAT" : "GERÇEK KANIT · ONAYLI TEMİNAT",
      real_confirmed_refund_e2e: short ? "GERÇEK İADE" : "GERÇEK KANIT · ONAYLI İADE",
      synthetic_walkthrough: short ? "SENTETİK" : "SENTETİK · ÖRNEK AKIŞ"
    };
    return labels[String(kind || "")] || "";
  }

  function moderationDisabledReason(status) {
    if (status === "awaiting_bond") return "Önce shielded teminat ödenmeli.";
    if (status === "bond_pending") return "Testnet onayı bekleniyor.";
    if (status === "refund_broadcast") return "İade yayınlandı; zincir onayı bekleniyor.";
    if (status === "refunded") return "İade zincirde onaylandı.";
    if (status === "kept") return "Spam kararıyla teminat tutuldu.";
    return "Bu bildirim şu anda karara açık değil.";
  }

  async function createSubmission(event) {
    event.preventDefault();
    if (state.snapshotReadOnly) return;
    hideError(el.formError);
    const body = el.feedbackMessage.value.trim();
    const refundAddress = el.refundAddress.value.trim();

    let valid = true;
    if (body.length < 4) {
      markInvalid(el.feedbackMessage, true);
      valid = false;
    } else {
      markInvalid(el.feedbackMessage, false);
    }

    if (!isValidTestnetAddress(refundAddress)) {
      markInvalid(el.refundAddress, true);
      valid = false;
    } else {
      markInvalid(el.refundAddress, false);
    }

    if (!valid) {
      showError(el.formError, "En az 4 karakterlik bir mesaj ve küçük harfli utest1… Unified Address gir.");
      (body.length < 4 ? el.feedbackMessage : el.refundAddress).focus();
      return;
    }

    const title = deriveTitle(body);
    setButtonBusy(el.createButton, true, "Talep oluşturuluyor");

    try {
      const data = await request("/submissions", {
        method: "POST",
        body: { title, body, refund_address: refundAddress }
      });
      state.current = normalizeSubmission(data);
      if (!state.current.id) throw new Error("Sunucu bildirim kimliği döndürmedi.");
      localStorage.setItem(STORAGE_KEY, state.current.id);
      state.selected = state.current;
      state.selectedId = state.current.id;
      showInvoice();
      startPolling();
      showToast("Ödeme talebi hazır. Teminat gönderilmeden mesaj moderasyona açılmaz.");
      await loadSubmissions().catch(() => null);
    } catch (error) {
      showError(el.formError, error.message || "Talep oluşturulamadı.");
    } finally {
      setButtonBusy(el.createButton, false);
    }
  }

  function showInvoice() {
    el.composerView.hidden = true;
    el.invoiceView.hidden = false;
    renderInvoice(state.current);
  }

  function showComposer() {
    stopPolling();
    state.current = null;
    localStorage.removeItem(STORAGE_KEY);
    el.invoiceView.hidden = true;
    el.composerView.hidden = false;
    hideError(el.invoiceError);
    el.feedbackMessage.focus();
  }

  function renderInvoice(submission) {
    if (!submission) return;
    applyBadge(el.invoiceStatusBadge, submission);
    el.invoiceCode.textContent = displayCode(submission.id);
    el.invoiceAmount.textContent = formatZec(submission.invoice.amountZec || healthBondAmount());
    el.paymentAddress.textContent = submission.invoice.address || "Adres sunucudan alınamadı";
    el.copyAddress.disabled = !submission.invoice.address;
    renderQr(submission.invoice.qrSvg);
    renderTimeline(submission);
    renderInvoiceOutcome(submission);

    const status = submission.status;
    const awaiting = status === "awaiting_bond";
    el.payButton.disabled = state.snapshotReadOnly || !awaiting || !submission.invoice.address;
    if (awaiting) {
      const mockMode = state.health?.mode === "mock" || state.health?.demo === true || submission.demo;
      setButtonLabel(
        el.payButton,
        state.snapshotReadOnly ? "Salt okunur kayıt · gönderim kapalı" : mockMode ? "Simülasyon ödemesini çalıştır" : "Test cüzdanıyla öde"
      );
      el.invoiceInstruction.textContent = state.snapshotReadOnly
        ? "Bu kayıt salt okunur; ödeme göndermez ve durumu değiştirmez."
        : mockMode
          ? "Bu sentetik demo akışıdır; ödeme zincire gönderilmez ve gerçek kanıt sayılmaz."
          : "Aşağıdaki tek kullanımlık adrese tam tutarı gönder.";
    } else if (status === "bond_pending") {
      setButtonLabel(el.payButton, "İşlem yayınlandı");
      el.invoiceInstruction.textContent = `Testnet işlemi görüldü; ${submission.bond.confirmations}/${submission.bond.requiredConfirmations || "?"} onay. Henüz kesinleşmedi.`;
    } else if (status === "moderation") {
      setButtonLabel(el.payButton, "Teminat doğrulandı");
      el.invoiceInstruction.textContent = "Shielded teminat zincirde doğrulandı. Geri bildirimin moderasyon sırasında.";
    } else if (status === "refund_broadcast") {
      setButtonLabel(el.payButton, "İade yayınlandı");
      el.invoiceInstruction.textContent = `İade işlemi yayınlandı; ${submission.refund.confirmations || 0} onay ile henüz kesinleşmesi bekleniyor.`;
    } else if (status === "refunded") {
      setButtonLabel(el.payButton, "İade onaylandı");
      el.invoiceInstruction.textContent = "Meşru geri bildirim kararı ve iade zincirde onaylandı.";
    } else if (status === "kept") {
      setButtonLabel(el.payButton, "Teminat tutuldu");
      el.invoiceInstruction.textContent = "Moderatör spam/kötüye kullanım kararı verdi; teminat operatör cüzdanında tutuldu.";
    } else if (status === "mismatch") {
      setButtonLabel(el.payButton, "Ödeme eşleşmedi");
      el.invoiceInstruction.textContent = submission.bond.mismatchReason || "Tutar veya memo ödeme talebiyle eşleşmedi.";
    } else {
      setButtonLabel(el.payButton, "Akış durdu");
      el.invoiceInstruction.textContent = submission.refund.error || "İşlem tamamlanamadı; ağ durumunu kontrol et.";
    }

    el.checkPayment.disabled = false;
    if (TERMINAL_STATES.has(status)) stopPolling();
  }

  async function payWithTestWallet() {
    if (state.snapshotReadOnly || !state.current?.id) return;
    hideError(el.invoiceError);
    setButtonBusy(el.payButton, true, "Testnet işlemi hazırlanıyor");

    try {
      const data = await request(`/submissions/${encodeURIComponent(state.current.id)}/pay`, {
        method: "POST",
        body: {}
      });
      state.current = normalizeSubmission(data?.submission || data);
      localStorage.setItem(STORAGE_KEY, state.current.id);
      renderInvoice(state.current);
      replaceInList(state.current);
      state.selected = state.current;
      state.selectedId = state.current.id;
      renderSubmissionList();
      renderModerationDetail();

      const mode = String(data?.mode || state.health?.mode || "").toLowerCase();
      const wording = mode === "mock"
        ? "Demo/mock ödeme olayı işlendi; gerçek testnet işlemi gibi sunulmuyor."
        : "Testnet işlemi yayınlandı. Onay gelene kadar ‘bekliyor’ olarak kalacak.";
      showToast(wording);
      startPolling();
      window.setTimeout(() => syncNetwork({ quiet: true }), 900);
    } catch (error) {
      showError(el.invoiceError, error.message || "Test cüzdanı ödemeyi başlatamadı.");
    } finally {
      setButtonBusy(el.payButton, false);
      renderInvoice(state.current);
    }
  }

  async function loadCurrentSubmission({ silent = false, announce = false } = {}) {
    if (!state.current?.id) return null;
    if (!silent) setButtonBusy(el.checkPayment, true, "Kontrol ediliyor");
    hideError(el.invoiceError);

    try {
      const data = await request(`/submissions/${encodeURIComponent(state.current.id)}`);
      const previousStatus = state.current.status;
      state.current = normalizeSubmission(data);
      renderInvoice(state.current);
      replaceInList(state.current);
      if (state.selectedId === state.current.id) state.selected = state.current;
      renderSubmissionList();
      renderModerationDetail();
      if (announce) showToast("Bildirim durumu zincir kayıtlarıyla yenilendi.");
      if (previousStatus !== state.current.status) {
        showToast(statusChangeMessage(state.current));
      }
      if (TERMINAL_STATES.has(state.current.status)) stopPolling();
      return state.current;
    } catch (error) {
      if (!silent) showError(el.invoiceError, error.message || "Durum alınamadı.");
      if (error.status === 404) {
        stopPolling();
        localStorage.removeItem(STORAGE_KEY);
      }
      throw error;
    } finally {
      if (!silent) setButtonBusy(el.checkPayment, false);
    }
  }

  async function syncNetwork(options = {}) {
    const quiet = Boolean(options.quiet);
    toggleSpin(el.syncButton, true);
    toggleSpin(el.refreshAll, true);
    el.syncButton.disabled = true;

    try {
      const data = await request("/sync", { method: "POST", body: {} });
      await Promise.allSettled([
        loadHealth(),
        loadSubmissions(),
        state.current?.id ? loadCurrentSubmission({ silent: true }) : Promise.resolve()
      ]);
      if (!quiet) {
        const updated = Number(data?.submissions_updated || 0);
        showToast(updated ? `${updated} bildirim zincir verisiyle güncellendi.` : "Zincir tarandı; yeni durum değişikliği yok.");
      }
    } catch (error) {
      if (!quiet) showToast(error.message || "Zincir eşitlenemedi.", "error");
    } finally {
      toggleSpin(el.syncButton, false);
      toggleSpin(el.refreshAll, false);
      el.syncButton.disabled = false;
    }
  }

  async function moderate(decision) {
    if (state.snapshotReadOnly) return;
    const submission = state.selected;
    if (!submission?.id || submission.status !== "moderation" || submission.canModerate === false || submission.moderation?.decision) return;
    const confirmed = window.confirm(decision === "refund"
      ? "Bu karar gerçek Zcash testnet iadesini başlatabilir ve bu sahne vakasını tüketir. Yalnız bir kez devam edilsin mi?"
      : "Bu karar teminatı operatör cüzdanında tutar ve bu sahne vakasını kapatır. Devam edilsin mi?");
    if (!confirmed) return;
    const button = decision === "refund" ? el.refundButton : el.keepButton;
    const other = decision === "refund" ? el.keepButton : el.refundButton;
    hideError(el.detailError);
    other.disabled = true;
    setButtonBusy(button, true, decision === "refund" ? "İade hazırlanıyor" : "Karar kaydediliyor");

    try {
      const data = await request(`/submissions/${encodeURIComponent(submission.id)}/moderate`, {
        method: "POST",
        body: {
          decision,
          note: decision === "refund"
            ? "Meşru geri bildirim — 0,01 TAZ teminat iadesi"
            : "Spam veya kötüye kullanım — teminat operatör cüzdanında tutuldu"
        }
      });
      const updated = normalizeSubmission(data?.submission || data);
      state.selected = updated;
      state.selectedId = updated.id;
      replaceInList(updated);
      if (state.current?.id === updated.id) {
        state.current = updated;
        renderInvoice(updated);
        if (!TERMINAL_STATES.has(updated.status)) startPolling();
      }
      renderSubmissionList();
      renderModerationDetail();

      if (decision === "refund") {
        showToast(updated.status === "refunded"
          ? "İade zincirde onaylandı."
          : updated.status === "refund_broadcast"
            ? "İade işlemi yayınlandı; kesinleşene kadar onay bekleniyor."
            : "İade işlemi hazırlanıyor; Zallet yayın kanıtı bekleniyor.");
      } else {
        showToast("Spam kararı kaydedildi; teminat yakılmadı, operatör cüzdanında tutuldu.");
      }
      window.setTimeout(() => loadSubmissions().catch(() => null), 500);
      if (decision === "refund") window.setTimeout(() => syncNetwork({ quiet: true }), 1100);
    } catch (error) {
      showDetailError(error.message || "Moderasyon kararı uygulanamadı.");
    } finally {
      setButtonBusy(button, false);
      renderModerationDetail();
    }
  }

  function renderTimeline(submission) {
    clearNode(el.statusTimeline);
    const status = submission?.status || "awaiting_bond";
    const stage = statusStage(status);
    const failed = status === "failure" || status === "mismatch";
    const required = submission?.bond.requiredConfirmations || state.health?.bond?.min_confirmations || "?";
    const confirmations = submission?.bond.confirmations || 0;

    const steps = [
      { title: "Talep", detail: "oluşturuldu" },
      { title: "Teminat", detail: status === "awaiting_bond" ? "bekleniyor" : "görüldü" },
      { title: "Testnet onayı", detail: `${confirmations}/${required} onay` },
      { title: status === "kept" ? "Karar" : "Karar / iade", detail: timelineLastDetail(submission) }
    ];

    steps.forEach((step, index) => {
      const item = document.createElement("li");
      if (index < stage || (index === 0 && submission)) item.className = "is-done";
      if (index === stage && !TERMINAL_STATES.has(status)) item.className = "is-current";
      if (failed && index === Math.max(1, stage)) item.className = "is-failed";
      if ((status === "refunded" || status === "kept") && index <= 3) item.className = "is-done";
      const strong = document.createElement("strong");
      strong.textContent = step.title;
      const small = document.createElement("small");
      small.textContent = step.detail;
      item.append(strong, small);
      el.statusTimeline.append(item);
    });
  }

  function timelineLastDetail(submission) {
    if (!submission) return "sırada";
    if (submission.status === "awaiting_bond" || submission.status === "bond_pending") return "sırada";
    if (submission.status === "moderation") return "bekliyor";
    if (submission.status === "refund_broadcast") return `${submission.refund.confirmations || 0} onay`;
    if (submission.status === "refunded") return "iade onaylı";
    if (submission.status === "kept") return "bond tutuldu";
    if (submission.status === "mismatch") return "eşleşmedi";
    return "başarısız";
  }

  function renderInvoiceOutcome(submission) {
    el.outcomeCard.hidden = true;
    el.outcomeCard.className = "outcome-card";
    clearNode(el.outcomeCard);

    if (submission.status === "refund_broadcast") {
      el.outcomeCard.hidden = false;
      el.outcomeCard.append(document.createTextNode("İade işlemi yayınlandı; bu henüz ‘iade edildi’ demek değil. Zincir onayı bekleniyor. "));
      const link = transactionLink(submission.refund.txid, submission.refund.explorerUrl, "Explorer’da izle ↗", !submission.demo);
      if (link) el.outcomeCard.append(link);
    } else if (submission.status === "refunded") {
      el.outcomeCard.hidden = false;
      el.outcomeCard.append(document.createTextNode("Meşru bulundu · 0,01 TAZ iadesi zincirde doğrulandı. "));
      const link = transactionLink(submission.refund.txid, submission.refund.explorerUrl, "İade işlemi ↗", !submission.demo);
      if (link) el.outcomeCard.append(link);
    } else if (submission.status === "kept") {
      el.outcomeCard.hidden = false;
      el.outcomeCard.classList.add("is-kept");
      el.outcomeCard.textContent = "Spam / kötüye kullanım kararı · teminat yakılmadı; operatör cüzdanında tutuldu.";
    } else if (submission.status === "mismatch" || submission.status === "failure") {
      el.outcomeCard.hidden = false;
      el.outcomeCard.classList.add("is-kept");
      el.outcomeCard.textContent = submission.bond.mismatchReason || submission.refund.error || "Akış tamamlanamadı. Destek için bildirim kodunu sakla.";
    }
  }

  function renderQr(rawSvg) {
    el.qrImage.hidden = true;
    el.qrPlaceholder.hidden = false;
    el.qrFrame.classList.remove("has-error");

    if (!rawSvg) {
      el.qrFrame.title = "QR verisi sunucudan gelmedi; adresi kopyalayabilirsin.";
      return;
    }

    try {
      const safeSource = safeSvgDataUrl(rawSvg);
      el.qrImage.onload = () => {
        el.qrPlaceholder.hidden = true;
        el.qrImage.hidden = false;
      };
      el.qrImage.onerror = () => {
        el.qrImage.hidden = true;
        el.qrPlaceholder.hidden = false;
        el.qrFrame.classList.add("has-error");
      };
      el.qrImage.src = safeSource;
      el.qrFrame.title = "Sunucunun ZIP-321 ödeme QR kodu";
    } catch (_error) {
      el.qrFrame.classList.add("has-error");
      el.qrFrame.title = "QR güvenli biçimde işlenemedi; adresi kopyalayabilirsin.";
    }
  }

  function safeSvgDataUrl(value) {
    if (typeof value !== "string" || value.length > 1_000_000) throw new Error("Geçersiz SVG");
    let svgText = value.trim();

    if (/^data:image\/svg\+xml/i.test(svgText)) {
      const comma = svgText.indexOf(",");
      if (comma < 0) throw new Error("Geçersiz SVG data URL");
      const metadata = svgText.slice(0, comma);
      const payload = svgText.slice(comma + 1);
      if (/;base64/i.test(metadata)) {
        const bytes = Uint8Array.from(atob(payload.replace(/\s/g, "")), (char) => char.charCodeAt(0));
        svgText = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } else {
        svgText = decodeURIComponent(payload);
      }
    }

    const parser = new DOMParser();
    const documentNode = parser.parseFromString(svgText, "image/svg+xml");
    if (documentNode.querySelector("parsererror") || documentNode.documentElement.localName !== "svg") {
      throw new Error("SVG ayrıştırılamadı");
    }

    const allowedTags = new Set(["svg", "g", "path", "rect", "circle", "polygon"]);
    const allowedAttributes = new Set([
      "xmlns", "viewBox", "width", "height", "fill", "d", "x", "y", "rx", "ry",
      "cx", "cy", "r", "points", "transform", "shape-rendering", "preserveAspectRatio"
    ]);

    Array.from(documentNode.querySelectorAll("*")).forEach((node) => {
      if (!allowedTags.has(node.localName)) {
        node.remove();
        return;
      }
      Array.from(node.attributes).forEach((attribute) => {
        if (!allowedAttributes.has(attribute.name) || /^on/i.test(attribute.name)) node.removeAttribute(attribute.name);
      });
    });

    const root = documentNode.documentElement;
    root.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    if (!root.getAttribute("viewBox")) {
      const width = Number.parseFloat(root.getAttribute("width")) || 256;
      const height = Number.parseFloat(root.getAttribute("height")) || 256;
      root.setAttribute("viewBox", `0 0 ${Math.min(width, 2048)} ${Math.min(height, 2048)}`);
    }
    root.setAttribute("width", "256");
    root.setAttribute("height", "256");

    const sanitized = new XMLSerializer().serializeToString(root);
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(sanitized)}`;
  }

  async function copyPaymentAddress() {
    const address = state.current?.invoice.address;
    if (!address) return;
    try {
      await copyText(address);
      const label = el.copyAddress.querySelector("span");
      const old = label.textContent;
      label.textContent = "Kopyalandı";
      showToast("Tek kullanımlık shielded ödeme adresi kopyalandı.");
      window.setTimeout(() => { label.textContent = old; }, 1600);
    } catch (_error) {
      showError(el.invoiceError, "Adres panoya kopyalanamadı; metni elle seçebilirsin.");
    }
  }

  async function copyText(text) {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "clipboard-helper";
    document.body.append(area);
    area.select();
    const successful = document.execCommand("copy");
    area.remove();
    if (!successful) throw new Error("Kopyalama başarısız");
  }

  async function resetDemo() {
    hideError(el.resetError);
    setButtonBusy(el.confirmReset, true, "Sıfırlanıyor");
    try {
      const data = await request("/demo/reset", { method: "POST", body: {} });
      stopPolling();
      state.current = null;
      state.selected = null;
      state.selectedId = null;
      state.submissions = [];
      localStorage.removeItem(STORAGE_KEY);
      el.feedbackForm.reset();
      updateCharacterCount();
      showComposer();
      closeDialog(el.resetDialog);
      await Promise.allSettled([loadSubmissions(), loadHealth()]);
      showToast(`${Number(data?.deleted || 0)} demo kaydı temizlendi. Zincirdeki işlemler değişmedi.`);
    } catch (error) {
      showError(el.resetError, error.message || "Demo sıfırlanamadı.");
    } finally {
      setButtonBusy(el.confirmReset, false);
    }
  }

  function normalizeSubmission(payload) {
    const source = payload?.submission || payload?.data || payload || {};
    const invoice = source.invoice || source.payment_request || {};
    const bond = source.bond || source.payment || {};
    const refund = source.refund || {};
    const moderation = source.moderation || {};

    return {
      id: String(firstDefined(source.id, source.public_id, source.submission_id, "")),
      title: String(firstDefined(source.title, "")),
      body: String(firstDefined(source.body, source.message, source.content, source.text, "")),
      status: normalizeStatus(firstDefined(source.status, source.state, "awaiting_bond")),
      createdAt: firstDefined(source.created_at, source.createdAt, null),
      updatedAt: firstDefined(source.updated_at, source.updatedAt, null),
      demo: Boolean(firstDefined(source.demo, false)),
      evidenceKind: String(firstDefined(source.evidence_kind, source.evidenceKind, "")),
      canModerate: Boolean(firstDefined(source.can_moderate, source.canModerate, !moderation.decision)),
      refundAddressHint: String(firstDefined(source.refund_address_hint, source.refundAddressHint, "")),
      invoice: {
        address: String(firstDefined(invoice.address, invoice.payment_address, source.payment_address, "")),
        uri: String(firstDefined(invoice.uri, invoice.zip321_uri, invoice.payment_uri, "")),
        amountZat: numberOrZero(firstDefined(invoice.amount_zat, source.amount_zat, state.health?.bond?.amount_zat)),
        amountZec: String(firstDefined(invoice.amount_zec, invoice.amount_taz, source.amount_zec, state.health?.bond?.amount_zec, "0.01000000")),
        memo: String(firstDefined(invoice.memo, "")),
        qrSvg: firstDefined(invoice.qr_svg, invoice.qrSvg, source.qr_svg, source.qrSvg, null)
      },
      bond: {
        txid: String(firstDefined(bond.txid, source.payment_txid, "")),
        confirmations: numberOrZero(firstDefined(bond.confirmations, source.confirmations, 0)),
        requiredConfirmations: numberOrZero(firstDefined(bond.required_confirmations, source.required_confirmations, state.health?.bond?.min_confirmations, 1)),
        explorerUrl: String(firstDefined(bond.explorer_url, source.payment_explorer_url, "")),
        mismatchReason: String(firstDefined(bond.mismatch_reason, ""))
      },
      refund: {
        operationId: String(firstDefined(refund.operation_id, "")),
        txid: String(firstDefined(refund.txid, source.refund_txid, "")),
        confirmations: numberOrZero(firstDefined(refund.confirmations, 0)),
        explorerUrl: String(firstDefined(refund.explorer_url, source.refund_explorer_url, "")),
        error: String(firstDefined(refund.error, ""))
      },
      moderation: {
        decision: String(firstDefined(moderation.decision, source.decision, "")),
        note: String(firstDefined(moderation.note, "")),
        decidedAt: firstDefined(moderation.decided_at, null)
      },
      timeline: Array.isArray(source.timeline) ? source.timeline : [],
      raw: source
    };
  }

  function normalizeStatus(value) {
    const status = String(value || "").toLowerCase().replace(/[\s-]+/g, "_");
    const aliases = {
      pending: "awaiting_bond",
      pending_payment: "awaiting_bond",
      payment_pending: "bond_pending",
      paid: "bond_pending",
      seen_unconfirmed: "bond_pending",
      confirmed: "moderation",
      pending_moderation: "moderation",
      ready: "moderation",
      refund_pending: "refund_broadcast",
      refund_queued: "refund_broadcast",
      spam_retained: "kept",
      retained: "kept",
      failed: "failure"
    };
    return aliases[status] || status || "awaiting_bond";
  }

  function statusInfo(submission) {
    const status = typeof submission === "string" ? normalizeStatus(submission) : submission.status;
    if (
      typeof submission === "object"
      && status === "moderation"
      && submission.moderation?.decision === "refund"
      && !submission.refund?.txid
    ) {
      return {
        label: "İade hazırlanıyor · Henüz yayınlanmadı",
        short: "İade hazırlanıyor",
        className: "status-pending"
      };
    }
    const confirmations = typeof submission === "object" ? submission.bond?.confirmations || 0 : 0;
    const required = typeof submission === "object" ? submission.bond?.requiredConfirmations || 1 : 1;
    const map = {
      awaiting_bond: { label: "Ödeme bekliyor", short: "Bekliyor", className: "status-pending" },
      bond_pending: { label: `Testnette bekliyor · ${confirmations}/${required}`, short: `${confirmations}/${required} onay`, className: "status-seen" },
      moderation: { label: "Zincirde onaylı · Moderasyonda", short: "Onaylı", className: "status-confirmed" },
      refund_broadcast: { label: "İade yayınlandı · Onay bekliyor", short: "İade bekliyor", className: "status-seen" },
      refunded: { label: "İade zincirde onaylandı", short: "İade onaylı", className: "status-refunded" },
      kept: { label: "Spam · Teminat tutuldu", short: "Tutuldu", className: "status-kept" },
      mismatch: { label: "Ödeme eşleşmedi", short: "Eşleşmedi", className: "status-failed" },
      failure: { label: "Akış hatası", short: "Hata", className: "status-failed" }
    };
    return map[status] || { label: status.toLocaleUpperCase("tr-TR"), short: status.toLocaleUpperCase("tr-TR"), className: "" };
  }

  function createStatusBadge(submission) {
    const badge = document.createElement("span");
    applyBadge(badge, submission, true);
    return badge;
  }

  function applyBadge(node, submission, short = false) {
    const info = statusInfo(submission);
    const label = state.snapshotReadOnly
      ? `${short ? "Kayıt" : "Offline kayıt"} · ${short ? info.short : info.label}`
      : short ? info.short : info.label;
    node.className = `status-badge ${info.className}`.trim();
    clearNode(node);
    const dot = document.createElement("i");
    dot.setAttribute("aria-hidden", "true");
    node.append(dot, document.createTextNode(` ${label}`));
    node.title = label;
  }

  function statusStage(status) {
    const stages = {
      awaiting_bond: 1,
      bond_pending: 2,
      moderation: 3,
      refund_broadcast: 3,
      refunded: 4,
      kept: 4,
      mismatch: 2,
      failure: 3
    };
    return stages[status] ?? 1;
  }

  function statusChangeMessage(submission) {
    if (submission.status === "bond_pending") return "Shielded ödeme görüldü; testnet onayları bekleniyor.";
    if (submission.status === "moderation") return "Teminat zincirde doğrulandı; bildirim moderasyona açıldı.";
    if (submission.status === "refund_broadcast") return "İade yayınlandı; henüz zincir onayı bekleniyor.";
    if (submission.status === "refunded") return "0,01 TAZ iadesi zincirde onaylandı.";
    if (submission.status === "kept") return "Moderatör spam kararı verdi; teminat tutuldu.";
    return `Durum güncellendi: ${statusInfo(submission).label}`;
  }

  function replaceInList(submission) {
    if (!submission?.id) return;
    const index = state.submissions.findIndex((item) => item.id === submission.id);
    if (index >= 0) state.submissions[index] = mergeSubmission(state.submissions[index], submission);
    else state.submissions.unshift(submission);
  }

  function mergeSubmission(oldValue, newValue) {
    if (!oldValue) return newValue;
    if (!newValue) return oldValue;
    return {
      ...oldValue,
      ...newValue,
      invoice: { ...oldValue.invoice, ...newValue.invoice },
      bond: { ...oldValue.bond, ...newValue.bond },
      refund: { ...oldValue.refund, ...newValue.refund },
      moderation: { ...oldValue.moderation, ...newValue.moderation },
      timeline: newValue.timeline?.length ? newValue.timeline : oldValue.timeline
    };
  }

  function startPolling() {
    stopPolling();
    if (!state.current || TERMINAL_STATES.has(state.current.status) || document.hidden) return;
    state.pollTimer = window.setInterval(async () => {
      state.pollCount += 1;
      await loadCurrentSubmission({ silent: true }).catch(() => null);
      if (state.pollCount % 3 === 0) {
        await Promise.allSettled([loadHealth(), loadSubmissions()]);
      }
    }, POLL_INTERVAL);
  }

  function stopPolling() {
    if (state.pollTimer) window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  function restoreCurrentSubmission() {
    try {
      const id = localStorage.getItem(STORAGE_KEY);
      if (!id || id.length > 160) return;
      state.current = normalizeSubmission({ id, status: "awaiting_bond" });
      showInvoice();
      loadCurrentSubmission({ silent: true }).then(startPolling).catch(() => null);
    } catch (_error) {
      localStorage.removeItem(STORAGE_KEY);
    }
  }

  function deriveTitle(body) {
    const firstLine = body.split(/\r?\n/).find((line) => line.trim())?.trim() || "Anonim geri bildirim";
    const clean = firstLine.replace(/\s+/g, " ");
    return clean.length > 72 ? `${clean.slice(0, 69).trimEnd()}…` : clean;
  }

  function healthBondAmount() {
    return String(state.health?.bond?.amount_zec || "0.01000000");
  }

  function formatZec(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0,01";
    return number.toFixed(8).replace(/0+$/, "").replace(/\.$/, "").replace(".", ",");
  }

  function formatNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? new Intl.NumberFormat("tr-TR").format(number) : String(value);
  }

  function relativeTime(value) {
    if (!value) return "şimdi";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    const seconds = Math.round((date.getTime() - Date.now()) / 1000);
    const formatter = new Intl.RelativeTimeFormat("tr", { numeric: "auto" });
    if (Math.abs(seconds) < 60) return formatter.format(seconds, "second");
    const minutes = Math.round(seconds / 60);
    if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
    const hours = Math.round(minutes / 60);
    if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
    const days = Math.round(hours / 24);
    return formatter.format(days, "day");
  }

  function displayCode(id) {
    const clean = String(id || "").replace(/[^a-z0-9]/gi, "").toUpperCase();
    if (!clean) return "#—";
    return `#${clean.length > 8 ? `${clean.slice(0, 4)}·${clean.slice(-4)}` : clean}`;
  }


  function transactionLink(txid, explicitUrl, label, allowFallback = true) {
    if (!txid && !explicitUrl) return null;
    const url = safeExplorerUrl(explicitUrl, txid, allowFallback);
    if (!url) return null;
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = label;
    link.title = txid ? `İşlem ${shortTxid(txid)}` : "Explorer kaydı";
    return link;
  }

  function safeExplorerUrl(explicitUrl, txid, allowFallback = true) {
    const offlineReplay = state.health?.mode === "mock" || state.health?.demo === true;
    if (offlineReplay) return null;
    let candidate = explicitUrl;
    if (!candidate && txid && allowFallback) {
      const base = state.health?.explorer_base_url || "https://blockexplorer.one/zcash/testnet/tx";
      candidate = String(base).includes("{txid}")
        ? String(base).replace("{txid}", encodeURIComponent(txid))
        : `${String(base).replace(/\/$/, "")}/${encodeURIComponent(txid)}`;
    }
    if (!candidate) return null;
    try {
      const parsed = new URL(candidate, window.location.origin);
      if (parsed.protocol !== "https:" && parsed.origin !== window.location.origin) return null;
      return parsed.href;
    } catch (_error) {
      return null;
    }
  }

  function shortTxid(txid) {
    const value = String(txid || "");
    return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-8)}` : value;
  }

  function validateField(field) {
    if (field === el.feedbackMessage) markInvalid(field, field.value.trim().length < 4 && field.value.length > 0);
    if (field === el.refundAddress) {
      const value = field.value.trim();
      markInvalid(field, value.length > 0 && !isValidTestnetAddress(value));
    }
  }

  function isValidTestnetAddress(value) {
    return /^utest1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{44,506}$/.test(value);
  }

  function markInvalid(field, invalid) {
    field.setAttribute("aria-invalid", String(Boolean(invalid)));
  }

  function updateCharacterCount() {
    const count = el.feedbackMessage.value.length;
    el.characterCount.textContent = `${count} / 320`;
    el.characterCount.classList.toggle("is-near-limit", count > 300);
    if (count >= 4) markInvalid(el.feedbackMessage, false);
  }

  function setButtonBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      if (!button.dataset.idleHtml) button.dataset.idleHtml = button.innerHTML;
      button.disabled = true;
      button.textContent = label || "İşleniyor";
      button.classList.add("loading-word");
      button.setAttribute("aria-busy", "true");
    } else {
      if (button.dataset.idleHtml) button.innerHTML = button.dataset.idleHtml;
      button.classList.remove("loading-word");
      button.removeAttribute("aria-busy");
      button.disabled = false;
    }
  }

  function setButtonLabel(button, label) {
    const span = button.querySelector("span");
    if (span) span.textContent = label;
    else button.textContent = label;
  }

  function toggleSpin(button, spinning) {
    if (!button) return;
    button.classList.toggle("is-spinning", Boolean(spinning));
    button.setAttribute("aria-busy", String(Boolean(spinning)));
  }

  function showError(node, message) {
    node.textContent = message;
    node.hidden = false;
  }

  function hideError(node) {
    node.hidden = true;
    node.textContent = "";
  }

  function showDetailError(message) {
    showError(el.detailError, message);
  }

  function showToast(message, type = "success") {
    const toast = document.createElement("div");
    toast.className = `toast${type === "error" ? " is-error" : ""}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");

    const icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.textContent = type === "error" ? "!" : "✓";
    const text = document.createElement("span");
    text.className = "toast-message";
    text.textContent = message;
    const close = document.createElement("button");
    close.className = "toast-close";
    close.type = "button";
    close.setAttribute("aria-label", "Bildirimi kapat");
    close.textContent = "×";
    close.addEventListener("click", () => removeToast(toast));
    toast.append(icon, text, close);
    el.toastStack.append(toast);
    window.setTimeout(() => removeToast(toast), type === "error" ? 7000 : 4800);
  }

  function removeToast(toast) {
    if (!toast?.isConnected || toast.classList.contains("is-leaving")) return;
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 220);
  }

  function openPolicyDialog() {
    openDialog(el.policyDialog);
  }

  function openDialog(dialog) {
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function closeDialog(dialog) {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  function closeOnBackdrop(event, dialog) {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    const inside = event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
    if (!inside) closeDialog(dialog);
  }

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function firstDefined(...values) {
    return values.find((value) => value !== undefined && value !== null);
  }

  function numberOrZero(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function toCamel(value) {
    return value.replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
  }
})();
