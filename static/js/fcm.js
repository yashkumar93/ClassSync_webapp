/**
 * Class Sync — Firebase Cloud Messaging (FCM) Web Push Client
 */
(function () {
  "use strict";

  // Check browser support
  if (!("Notification" in window) || !("serviceWorker" in navigator)) {
    console.info("[ClassSync FCM] Push notifications are not supported in this browser.");
    return;
  }

  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
      const cookies = document.cookie.split(";");
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === name + "=") {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  function showToast(title, message, type = "info") {
    let container = document.getElementById("cs-toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "cs-toast-container";
      container.style.position = "fixed";
      container.style.top = "1.5rem";
      container.style.right = "1.5rem";
      container.style.zIndex = "99999";
      container.style.display = "flex";
      container.style.flexDirection = "column";
      container.style.gap = "0.75rem";
      container.style.maxWidth = "360px";
      container.style.pointerEvents = "none";
      document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.style.pointerEvents = "auto";
    toast.style.background = "#ffffff";
    toast.style.color = "#1e293b";
    toast.style.borderRadius = "10px";
    toast.style.padding = "0.85rem 1.1rem";
    toast.style.boxShadow = "0 10px 25px -5px rgba(0,0,0,0.15), 0 8px 10px -6px rgba(0,0,0,0.1)";
    toast.style.borderLeft = type === "success" ? "4px solid #10b981" : type === "error" ? "4px solid #ef4444" : "4px solid #2563eb";
    toast.style.animation = "slideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1)";
    toast.style.display = "flex";
    toast.style.alignItems = "flex-start";
    toast.style.gap = "0.75rem";

    const icon = type === "success" ? "bi-check-circle-fill text-success" : type === "error" ? "bi-exclamation-triangle-fill text-danger" : "bi-bell-fill text-primary";

    toast.innerHTML = `
      <i class="bi ${icon}" style="font-size:1.2rem; margin-top:2px;"></i>
      <div style="flex:1;">
        <div style="font-weight:600; font-size:0.92rem; margin-bottom:2px;">${title}</div>
        <div style="font-size:0.83rem; color:#64748b; line-height:1.35;">${message}</div>
      </div>
      <button type="button" style="background:none; border:none; color:#94a3b8; cursor:pointer; font-size:1rem; padding:0;" onclick="this.parentElement.remove()">
        <i class="bi bi-x"></i>
      </button>
    `;

    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transition = "opacity 0.4s ease";
      setTimeout(() => toast.remove(), 400);
    }, 6000);
  }

  function incrementNavbarBadge() {
    const bell = document.querySelector(".cs-bell");
    if (!bell) return;
    let badge = bell.querySelector(".badge");
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "badge";
      bell.appendChild(badge);
    }
    const current = parseInt(badge.textContent || "0", 10);
    badge.textContent = current + 1;
    badge.style.display = "inline-block";
  }

  // Retrieve Firebase Config
  let config = window.FIREBASE_CONFIG;
  if (!config) {
    const configEl = document.getElementById("firebase-config");
    if (configEl) {
      try {
        config = JSON.parse(configEl.textContent);
      } catch (e) {
        console.error("[ClassSync FCM] Error parsing firebase-config:", e);
      }
    }
  }

  if (!config || !config.apiKey || config.apiKey === "your-firebase-api-key") {
    console.info("[ClassSync FCM] Firebase configuration not set or incomplete.");
    return;
  }

  // Initialize Firebase App
  let app;
  try {
    if (typeof firebase !== "undefined") {
      if (!firebase.apps.length) {
        app = firebase.initializeApp(config);
      } else {
        app = firebase.app();
      }
    } else {
      console.warn("[ClassSync FCM] Firebase compat SDK not loaded.");
      return;
    }
  } catch (err) {
    console.error("[ClassSync FCM] Firebase initialization error:", err);
    return;
  }

  const messaging = firebase.messaging();

  async function registerDeviceToken(token) {
    try {
      const csrfToken = getCookie("csrftoken");
      const res = await fetch("/notifications/device-token/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify({ token: token, platform: "web" }),
      });
      const data = await res.json();
      if (res.ok) {
        console.info("[ClassSync FCM] Device token registered successfully with backend.");
        updatePushButtonsUI(true);
      } else {
        console.warn("[ClassSync FCM] Backend device token registration failed:", data);
      }
    } catch (err) {
      console.error("[ClassSync FCM] Error sending device token to backend:", err);
    }
  }

  async function requestPushPermission() {
    try {
      const permission = await Notification.requestPermission();
      if (permission === "granted") {
        console.info("[ClassSync FCM] Notification permission granted.");
        const swReg = await navigator.serviceWorker.register("/firebase-messaging-sw.js");
        await navigator.serviceWorker.ready;

        const tokenParams = { serviceWorkerRegistration: swReg };
        if (window.FIREBASE_VAPID_KEY) {
          tokenParams.vapidKey = window.FIREBASE_VAPID_KEY;
        }

        const token = await messaging.getToken(tokenParams);
        if (token) {
          await registerDeviceToken(token);
          showToast("Push Notifications Enabled", "You'll now receive real-time alerts for absences, timetable updates, and assignments.", "success");
        } else {
          showToast("Permission Notice", "Permission granted, but no FCM token was generated. If using Web Push, ensure a VAPID key is configured.", "info");
        }
      } else if (permission === "denied") {
        showToast("Notifications Blocked", "Push notifications were blocked. You can enable them in your browser site settings.", "error");
        updatePushButtonsUI(false);
      }
    } catch (err) {
      console.error("[ClassSync FCM] Error requesting push permission:", err);
      showToast("Push Setup Issue", err.message || "Failed to enable push notifications.", "error");
    }
  }

  function updatePushButtonsUI(isGranted) {
    const buttons = document.querySelectorAll(".cs-fcm-enable-btn");
    buttons.forEach((btn) => {
      if (isGranted) {
        btn.innerHTML = '<i class="bi bi-check2-circle text-success"></i> Push Active';
        btn.classList.add("disabled");
        btn.disabled = true;
      } else {
        btn.innerHTML = '<i class="bi bi-bell"></i> Enable Push Notifications';
        btn.classList.remove("disabled");
        btn.disabled = false;
      }
    });

    const statusBadges = document.querySelectorAll(".cs-fcm-status-badge");
    statusBadges.forEach((badge) => {
      if (isGranted) {
        badge.className = "cs-fcm-status-badge cs-badge success";
        badge.textContent = "Active";
      } else if (Notification.permission === "denied") {
        badge.className = "cs-fcm-status-badge cs-badge danger";
        badge.textContent = "Blocked";
      } else {
        badge.className = "cs-fcm-status-badge cs-badge warning";
        badge.textContent = "Not Enabled";
      }
    });
  }

  // Auto-register token if permission already granted
  if (Notification.permission === "granted") {
    navigator.serviceWorker
      .register("/firebase-messaging-sw.js")
      .then((swReg) => navigator.serviceWorker.ready.then(() => swReg))
      .then((swReg) => {
        const tokenParams = { serviceWorkerRegistration: swReg };
        if (window.FIREBASE_VAPID_KEY) {
          tokenParams.vapidKey = window.FIREBASE_VAPID_KEY;
        }
        return messaging.getToken(tokenParams);
      })
      .then((token) => {
        if (token) registerDeviceToken(token);
      })
      .catch((err) => {
        console.debug("[ClassSync FCM] Auto-token fetch:", err);
      });
  }

  // Handle Foreground Messages
  messaging.onMessage((payload) => {
    console.log("[ClassSync FCM] Foreground message received:", payload);
    const title = payload.notification?.title || payload.data?.title || "Class Sync";
    const body = payload.notification?.body || payload.data?.body || payload.data?.message || "You have a new update.";
    showToast(title, body, "info");
    incrementNavbarBadge();
  });

  // Expose global helpers
  window.ClassSyncFCM = {
    requestPermission: requestPushPermission,
    sendTestPush: async function () {
      try {
        const csrfToken = getCookie("csrftoken");
        const res = await fetch("/notifications/test-push/", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
          },
        });
        const data = await res.json();
        if (res.ok) {
          showToast("Test Notification Dispatched", data.message || "Notification sent via FCM!", "success");
        } else {
          showToast("Test Notification Warning", data.message || "Could not dispatch test push.", "error");
        }
      } catch (err) {
        showToast("Error", err.message || "Failed to trigger test push.", "error");
      }
    },
  };

  // Wire up any buttons on page load
  document.addEventListener("DOMContentLoaded", () => {
    updatePushButtonsUI(Notification.permission === "granted");
    document.querySelectorAll(".cs-fcm-enable-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        requestPushPermission();
      });
    });
    document.querySelectorAll(".cs-fcm-test-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        window.ClassSyncFCM.sendTestPush();
      });
    });
  });
})();
