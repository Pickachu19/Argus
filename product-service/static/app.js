document.querySelectorAll('.js-auto-dismiss').forEach((element) => {
  window.setTimeout(() => bootstrap.Alert.getOrCreateInstance(element).close(), 5000);
});

const deleteModal = document.getElementById('deleteModal');
if (deleteModal) {
  deleteModal.addEventListener('show.bs.modal', (event) => {
    const trigger = event.relatedTarget;
    document.getElementById('deleteForm').action = trigger.dataset.deleteUrl;
    document.getElementById('deleteProductName').textContent = trigger.dataset.productName;
  });
}

const imageInput = document.getElementById('image');
const imagePreview = document.getElementById('imagePreview');
if (imageInput && imagePreview) {
  imageInput.addEventListener('change', () => {
    const [file] = imageInput.files;
    if (!file) return;
    const image = document.createElement('img');
    image.src = URL.createObjectURL(file);
    image.alt = `Preview of ${file.name}`;
    image.addEventListener('load', () => URL.revokeObjectURL(image.src), { once: true });
    imagePreview.replaceChildren(image);
  });
}

document.querySelectorAll('.js-submit-form').forEach((form) => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button[type="submit"]');
    if (!button || !form.checkValidity()) return;
    button.disabled = true;
    button.textContent = button.dataset.loadingText || 'Saving…';
  });
});
