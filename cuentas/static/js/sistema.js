/* Mejoras progresivas del sistema de inventario (D-58). Todo funciona sin este archivo.
   Sin librerias ni recursos externos. */
(function () {
  "use strict";
  document.documentElement.classList.add("con-js");

  // Atajo «/»: enfoca el buscador de la pagina (se ignora si ya se esta escribiendo en un campo).
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) { return; }
    var a = document.activeElement, tag = a && a.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (a && a.isContentEditable)) { return; }
    var buscador = document.querySelector("input[type=search]");
    if (buscador) { e.preventDefault(); buscador.focus(); buscador.select(); }
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
