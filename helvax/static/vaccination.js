// Vaccination form: a product from the list ticks its antigens (read-only); "Anderer Impfstoff" lets the
// user pick them. Without JavaScript the server takes the antigens of the product anyway.
(function () {
  var select = document.getElementById('product');
  var other = document.getElementById('other-product');
  var boxes = document.querySelectorAll('#antigen-boxes input[type=checkbox]');
  if (!select) return;
  function update() {
    var option = select.options[select.selectedIndex];
    var isOther = select.value === 'other';
    other.hidden = !isOther;
    document.getElementById('product_name').required = isOther;
    if (isOther) {
      boxes.forEach(function (b) { b.disabled = false; });
      return;
    }
    var antigens = (option && option.dataset.antigens || '').split(' ');
    boxes.forEach(function (b) {
      b.checked = antigens.indexOf(b.value) !== -1;
      b.disabled = true;  // disabled boxes are not submitted; the server uses the product's antigens
    });
  }
  select.addEventListener('change', update);
  update();
})();
