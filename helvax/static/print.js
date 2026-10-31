// Print view: the button opens the browser's print dialog ("Save as PDF" is one of its printers)
(function () {
  var button = document.getElementById('print-button');
  if (button) button.addEventListener('click', function () { window.print(); });
})();
