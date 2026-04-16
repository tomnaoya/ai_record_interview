/* admin.js */

// サイドバートグル（モバイル）
document.addEventListener('DOMContentLoaded', function () {

  // オーバーレイクリックでサイドバーを閉じる
  document.addEventListener('click', function (e) {
    const sidebar = document.querySelector('.sidebar');
    const toggle = document.querySelector('.sidebar-toggle');
    if (!sidebar) return;
    if (sidebar.classList.contains('open') &&
        !sidebar.contains(e.target) &&
        e.target !== toggle) {
      sidebar.classList.remove('open');
    }
  });

  // フラッシュメッセージの自動消去
  setTimeout(function () {
    document.querySelectorAll('.alert').forEach(function (el) {
      el.style.transition = 'opacity .5s';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 500);
    });
  }, 4000);

  // 面接リンクのコピー
  document.querySelectorAll('.copy-link-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const url = btn.dataset.url;
      navigator.clipboard.writeText(url).then(function () {
        const orig = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-check"></i>';
        btn.style.color = '#16a34a';
        setTimeout(function () {
          btn.innerHTML = orig;
          btn.style.color = '';
        }, 1500);
      });
    });
  });

  // confirm 付き削除フォーム（data-confirm 属性で制御）
  document.querySelectorAll('[data-confirm]').forEach(function (el) {
    el.addEventListener('submit', function (e) {
      if (!confirm(el.dataset.confirm)) e.preventDefault();
    });
  });

});
