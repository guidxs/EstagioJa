// Pequena interação: mostra a mensagem de carregamento e evita cliques duplicados na busca.
(function () {
  var form = document.getElementById("search-form");
  var button = document.getElementById("search-button");
  var loading = document.getElementById("loading");
  if (!form) return;

  form.addEventListener("submit", function () {
    button.disabled = true;
    button.textContent = "Buscando...";
    if (loading) loading.hidden = false;
  });

  // Ao voltar pelo botão "voltar" do navegador, restaura o estado do botão
  window.addEventListener("pageshow", function () {
    button.disabled = false;
    button.textContent = "Buscar vagas";
    if (loading) loading.hidden = true;
  });
})();
