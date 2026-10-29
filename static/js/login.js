/* 登录 / 注册页逻辑。 */
(function () {
  "use strict";

  if (API.token()) {
    window.location.href = "/groups/";
    return;
  }

  var loginForm = document.getElementById("login-form");
  var registerForm = document.getElementById("register-form");

  loginForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    var username = document.getElementById("login-username").value.trim();
    var password = document.getElementById("login-password").value;

    if (!username || !password) {
      showError("请填写用户名和密码");
      return;
    }

    try {
      var data = await API.post("/api/auth/login/", {
        username: username,
        password: password,
      });
      API.setSession(data.token, data.user.username);
      window.location.href = "/groups/";
    } catch (error) {
      showError(error.detail);
    }
  });

  registerForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    var username = document.getElementById("register-username").value.trim();
    var password = document.getElementById("register-password").value;
    var nickname = document.getElementById("register-nickname").value.trim();
    var email = document.getElementById("register-email").value.trim();

    if (!username || !password) {
      showError("请填写用户名和密码");
      return;
    }
    if (password.length < 6) {
      showError("密码至少 6 位");
      return;
    }

    try {
      var data = await API.post("/api/auth/register/", {
        username: username,
        password: password,
        nickname: nickname,
        email: email,
      });
      API.setSession(data.token, data.user.username);
      showSuccess("注册成功，正在进入群组列表");
      window.location.href = "/groups/";
    } catch (error) {
      showError(error.detail);
    }
  });
})();
