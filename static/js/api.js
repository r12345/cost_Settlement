/* 全局 API 封装：Token 认证、统一错误处理、Toast 提示、XSS 转义。 */
(function () {
  "use strict";

  var TOKEN_KEY = "token";
  var USERNAME_KEY = "username";

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function setSession(token, username) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USERNAME_KEY, username || "");
  }

  function clearSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USERNAME_KEY);
  }

  function toLogin() {
    clearSession();
    if (window.location.pathname.indexOf("/login") !== 0) {
      window.location.href = "/login/";
    }
  }

  async function request(method, url, body) {
    var headers = {};
    var token = getToken();
    if (token) {
      headers["Authorization"] = "Token " + token;
    }

    var options = { method: method, headers: headers };
    if (body !== undefined && body !== null) {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }

    var response;
    try {
      response = await fetch(url, options);
    } catch (error) {
      throw {
        detail: "网络请求失败，请确认后端服务正在运行",
        code: "network_error",
        status: 0,
      };
    }

    if (response.status === 401) {
      toLogin();
      throw { detail: "登录已失效，请重新登录", code: "unauthorized", status: 401 };
    }

    var data = null;
    if (response.status !== 204) {
      try {
        data = await response.json();
      } catch (error) {
        data = null;
      }
    }

    if (!response.ok) {
      throw {
        detail:
          (data && (data.detail || data.reason)) ||
          "请求失败（HTTP " + response.status + "）",
        code: data && data.code,
        status: response.status,
        data: data,
      };
    }
    return data;
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function showToast(message, variant) {
    var container = document.getElementById("toast-container");
    if (!container) {
      return;
    }
    var element = document.createElement("div");
    element.className = "toast align-items-center text-bg-" + variant + " border-0";
    element.setAttribute("role", "alert");
    element.innerHTML =
      '<div class="d-flex"><div class="toast-body">' +
      escapeHtml(message) +
      '</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
    container.appendChild(element);
    element.addEventListener("hidden.bs.toast", function () {
      element.remove();
    });
    new bootstrap.Toast(element, { delay: 4000 }).show();
  }

  function showError(message) {
    showToast(message || "操作失败", "danger");
  }

  function showSuccess(message) {
    showToast(message || "操作成功", "success");
  }

  function requireAuth() {
    if (!getToken()) {
      window.location.href = "/login/";
      return false;
    }
    return true;
  }

  async function logout() {
    try {
      await request("POST", "/api/auth/logout/");
    } catch (error) {
      /* 令牌可能已经失效，忽略即可 */
    }
    clearSession();
    window.location.href = "/login/";
  }

  function money(value) {
    var number = Number(value);
    if (isNaN(number)) {
      return "0.00";
    }
    return number.toFixed(2);
  }

  window.API = {
    get: function (url) {
      return request("GET", url);
    },
    post: function (url, body) {
      return request("POST", url, body === undefined ? {} : body);
    },
    patch: function (url, body) {
      return request("PATCH", url, body);
    },
    delete: function (url) {
      return request("DELETE", url);
    },
    token: getToken,
    setSession: setSession,
    clearSession: clearSession,
    requireAuth: requireAuth,
    logout: logout,
    money: money,
  };

  window.showError = showError;
  window.showSuccess = showSuccess;
  window.escapeHtml = escapeHtml;
})();
