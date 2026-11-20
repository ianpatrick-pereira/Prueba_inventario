/* Mejoras progresivas del sistema de inventario (D-58). Todo funciona sin este archivo.
   Sin librerias ni recursos externos. */
(function () {
  "use strict";
  document.documentElement.classList.add("con-js");

  // Atajos de busqueda: «/» (Shift+7 en teclado latinoamericano, o el «/» del teclado numerico) y
  // Ctrl+K (Cmd+K en Mac). Enfocan el buscador de la pagina; si no hay buscador, no se hace nada.
  document.addEventListener("keydown", function (e) {
    var buscador = document.querySelector("input[type=search]");
    if (!buscador) { return; }
    var esCtrlK = (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === "k" || e.key === "K");
    if (esCtrlK) {  // funciona tambien mientras se escribe en otro campo
      e.preventDefault(); buscador.focus(); buscador.select(); return;
    }
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) { return; }
    var a = document.activeElement, tag = a && a.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (a && a.isContentEditable)) { return; }
    e.preventDefault(); buscador.focus(); buscador.select();
  });

  // Botones «Imprimir» (data-imprimir): abren el cuadro de impresion del navegador.
  document.querySelectorAll("[data-imprimir]").forEach(function (b) {
    b.addEventListener("click", function () { window.print(); });
  });

  // El sufijo de unidad de los campos de stock sigue lo que se escribe en «Unidad».
  var unidad = document.getElementById("id_unidad");
  if (unidad) {
    var sufijos = document.querySelectorAll("[data-sufijo-unidad]");
    unidad.addEventListener("input", function () {
      sufijos.forEach(function (s) { s.textContent = unidad.value || "unidad"; });
    });
  }
})();
