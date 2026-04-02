// app.js — Interactions CyberScore

// Fermeture automatique des flash messages après 5 secondes
document.addEventListener('DOMContentLoaded', function () {
  const flashes = document.querySelectorAll('.flash');
  flashes.forEach(function (flash) {
    setTimeout(function () {
      flash.style.transition = 'opacity 0.5s';
      flash.style.opacity = '0';
      setTimeout(function () { flash.remove(); }, 500);
    }, 5000);
  });

  // Confirmation avant les actions destructives (annulation d'événement)
  // (déjà géré inline dans les templates, mais on centralise ici si besoin)
});
