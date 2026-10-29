/* 群组列表页：加载、创建、跳转。 */
(function () {
  "use strict";

  if (!API.requireAuth()) {
    return;
  }

  var grid = document.getElementById("group-grid");
  var empty = document.getElementById("group-empty");
  var form = document.getElementById("create-group-form");
  var modalElement = document.getElementById("create-group-modal");

  function cardHtml(group) {
    var roleClass =
      group.my_role === "admin" ? "text-bg-primary" : "text-bg-secondary";
    var roleText = group.my_role === "admin" ? "管理员" : "成员";
    return (
      '<div class="card h-100 shadow-sm group-card">' +
      '<div class="card-body">' +
      '<div class="d-flex justify-content-between align-items-start mb-2">' +
      '<h5 class="card-title mb-0">' +
      escapeHtml(group.name) +
      "</h5>" +
      '<span class="badge ' +
      roleClass +
      '">' +
      roleText +
      "</span>" +
      "</div>" +
      '<p class="card-text text-muted small mb-2">' +
      escapeHtml(group.description || "暂无描述") +
      "</p>" +
      '<span class="badge text-bg-light border">' +
      Number(group.member_count) +
      " 人</span>" +
      "</div></div>"
    );
  }

  async function loadGroups() {
    var data = await API.get("/api/groups/?page_size=100");
    var groups = data.results || [];
    grid.innerHTML = "";
    empty.classList.toggle("d-none", groups.length > 0);

    groups.forEach(function (group) {
      var column = document.createElement("div");
      column.className = "col-lg-4 col-md-6";
      column.innerHTML = cardHtml(group);
      column
        .querySelector(".group-card")
        .addEventListener("click", function () {
          window.location.href = "/groups/" + group.id + "/";
        });
      grid.appendChild(column);
    });
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    var name = document.getElementById("group-name").value.trim();
    var description = document.getElementById("group-description").value.trim();

    if (!name) {
      showError("请填写群组名称");
      return;
    }

    try {
      await API.post("/api/groups/", { name: name, description: description });
      bootstrap.Modal.getInstance(modalElement).hide();
      form.reset();
      showSuccess("群组创建成功");
      await loadGroups();
    } catch (error) {
      showError(error.detail);
    }
  });

  loadGroups().catch(function (error) {
    showError(error.detail);
  });
})();
