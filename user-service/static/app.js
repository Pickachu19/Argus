document.querySelectorAll('.js-auto-dismiss').forEach((element) => {
  window.setTimeout(() => bootstrap.Alert.getOrCreateInstance(element).close(), 5000);
});

document.querySelectorAll('.js-submit-form').forEach((form) => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('input[type="submit"], button[type="submit"]');
    if (!button || !form.checkValidity()) return;
    button.disabled = true;
    if (button.dataset.loadingText) button.value = button.dataset.loadingText;
  });
});

document.querySelectorAll('form[data-confirm]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (!window.confirm(form.dataset.confirm)) {
      event.preventDefault();
    }
  });
});
