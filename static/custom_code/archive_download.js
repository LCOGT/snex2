function showProgress(status, text) {
  status.innerHTML = '<span class="d-inline-flex align-items-center">' +
    '<span class="progress flex-shrink-0 me-2" style="width: 10rem; height: 1rem;">' +
    '<span class="progress-bar progress-bar-striped progress-bar-animated w-100" role="progressbar"></span></span>' +
    '<span></span></span>';
  status.querySelector('span > span:last-child').textContent = text;
}

function downloadFrames(url, progressUrl, basenames, csrfToken, button, status, label) {
  const token = String(Date.now());
  const frame = document.createElement('iframe');
  frame.name = 'frames-download-' + token;
  frame.hidden = true;
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = url;
  form.target = frame.name;
  [['csrfmiddlewaretoken', csrfToken], ['basenames', JSON.stringify(Array.from(new Set(basenames)))], ['download_token', token]].forEach(function (field) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = field[0];
    input.value = field[1];
    form.appendChild(input);
  });
  button.disabled = true;
  showProgress(status, label);
  let timer;
  function done(message) {
    clearInterval(timer);
    button.disabled = false;
    status.textContent = message;
  }
  let waiting = false;
  timer = setInterval(function () {
    if (document.cookie.split('; ').includes('download_token=' + token)) {
      document.cookie = 'download_token=; Max-Age=0; path=/';
      done('Download started. See your browser\'s downloads.');
      return;
    }
    fetch(progressUrl + '?token=' + token).then((response) => response.json()).then(function (progress) {
      const bar = status.querySelector('.progress-bar');
      if (!button.disabled || waiting || !bar || !progress.total) { return; }
      if (progress.done < progress.total) {
        bar.classList.remove('w-100');
        bar.style.width = Math.max(3, 100 * progress.done / progress.total) + '%';
        status.querySelector('span > span:last-child').textContent = label + ' (' + progress.done + ' of ' + progress.total + ')';
      } else {
        waiting = true;
        showProgress(status, 'Waiting for the LCO Archive to Start the Zip');
      }
    }).catch(() => {});
  }, 1000);
  document.body.appendChild(frame);
  frame.addEventListener('load', function () {
    if (frame.contentWindow.location.href === 'about:blank') { return; }
    const message = 'Download failed: ' + (frame.contentDocument.body.textContent.trim().slice(0, 200) || 'unknown error');
    done(message);
    frame.remove();
    const banner = document.createElement('div');
    banner.className = 'alert alert-danger alert-dismissible';
    banner.setAttribute('role', 'alert');
    banner.textContent = message;
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'btn-close';
    close.setAttribute('data-bs-dismiss', 'alert');
    close.setAttribute('aria-label', 'close');
    banner.appendChild(close);
    const main = document.querySelector('main') || document.body;
    main.prepend(banner);
    banner.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  document.body.appendChild(form);
  form.submit();
  form.remove();
}
