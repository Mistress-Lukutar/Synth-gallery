/**
 * Toast notification system - fixed top-right, auto-dismiss, multiple toasts.
 */
(function () {
    'use strict';

    function ensureContainer() {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            document.body.appendChild(container);
        }
        return container;
    }

    window.showToast = function (message, isError) {
        if (typeof isError === 'undefined') isError = false;
        const container = ensureContainer();
        const toast = document.createElement('div');
        toast.className = 'toast ' + (isError ? 'toast-error' : 'toast-success');
        toast.textContent = message;
        container.appendChild(toast);

        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                toast.classList.add('toast-show');
            });
        });

        setTimeout(function () {
            toast.classList.remove('toast-show');
            toast.addEventListener('transitionend', function () {
                toast.remove();
            }, { once: true });
        }, 5000);
    };
})();
