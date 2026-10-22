(function () {
  if (window.snexSpectra) { return; }
  const cache = {};
  let overlayRequest = 0;

  function load(url) {
    if (!cache[url]) {
      cache[url] = fetch(url).then(function (response) {
        if (!response.ok) { throw new Error(response.status); }
        return response.json();
      });
      cache[url].catch(function () { delete cache[url]; });
    }
    return cache[url];
  }

  function layout(extra) {
    return Object.assign({
      height: 350,
      margin: { l: 60, r: 20, t: 10, b: 40 },
      xaxis: { title: 'Wavelength (Å)', showgrid: false },
      yaxis: { title: 'Flux', tickformat: '.1e' },
      legend: { orientation: 'h', y: -0.2 },
      hovermode: 'closest',
    }, extra || {});
  }

  function modal() {
    let el = document.getElementById('spectrum-view-modal');
    if (!el) {
      el = document.createElement('div');
      el.id = 'spectrum-view-modal';
      el.className = 'modal fade';
      el.tabIndex = -1;
      el.innerHTML = '<div class="modal-dialog modal-xl"><div class="modal-content">' +
        '<div class="modal-header"><h5 class="modal-title"></h5>' +
        '<a class="btn btn-sm btn-outline-primary ms-3 spectrum-view-target" target="_blank">Open target page</a>' +
        '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button></div>' +
        '<div class="modal-body"><div class="spectrum-view-plot"></div></div></div></div>';
      document.body.appendChild(el);
    }
    return el;
  }

  function show(url) {
    const el = modal();
    const title = el.querySelector('.modal-title');
    const link = el.querySelector('.spectrum-view-target');
    const plot = el.querySelector('.spectrum-view-plot');
    title.textContent = 'Loading...';
    link.classList.add('d-none');
    Plotly.purge(plot);
    plot.innerHTML = '';
    bootstrap.Modal.getOrCreateInstance(el).show();
    load(url).then(function (data) {
      title.textContent = data.label;
      link.href = data.target_url;
      link.classList.remove('d-none');
      if (!data.wavelength.length) { plot.innerHTML = '<div class="text-muted p-3">No plottable data for this spectrum.</div>'; return; }
      setTimeout(function () {
        Plotly.newPlot(plot, [{ x: data.wavelength, y: data.flux, mode: 'lines', line: { color: 'black' }, name: data.label }],
                       layout({ showlegend: false }), { responsive: true });
      }, 200);
    }).catch(function (error) {
      title.textContent = 'Could not load the spectrum (' + error.message + ')';
    });
  }

  function overlay(div, urls) {
    const request = ++overlayRequest;
    if (!urls.length) {
      Plotly.purge(div);
      div.innerHTML = '<div class="text-muted p-3">No spectra selected.</div>';
      return;
    }
    Promise.all(urls.map(function (url) { return load(url).catch(function () { return null; }); })).then(function (spectra) {
      if (request !== overlayRequest) { return; }
      const traces = spectra.filter(function (data) { return data && data.wavelength.length; }).map(function (data) {
        return { x: data.wavelength, y: data.flux, mode: 'lines', name: data.label };
      });
      if (!div.querySelector('.plot-container')) { div.innerHTML = ''; }
      Plotly.react(div, traces, layout({ height: 450 }), { responsive: true });
    });
  }

  window.snexSpectra = { show: show, overlay: overlay };
})();
