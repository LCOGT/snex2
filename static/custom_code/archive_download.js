async function findFrameIds(basenames, url, csrfToken, status) {
  const CHUNK_SIZE = 500;
  const unique = Array.from(new Set(basenames));
  const frameIds = [];
  for (let i = 0; i < unique.length; i += CHUNK_SIZE) {
    status.textContent = 'Finding frames in the LCO archive: ' + i + ' of ' + unique.length + ' looked up...';
    const body = new FormData();
    body.append('basenames', JSON.stringify(unique.slice(i, i + CHUNK_SIZE)));
    const resp = await fetch(url, { method: 'POST', body: body, headers: { 'X-CSRFToken': csrfToken } });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error((data.error || resp.status) + ' (after ' + i + ' of ' + unique.length + ' frames)');
    }
    frameIds.push(...data.frame_ids);
  }
  return frameIds;
}

async function downloadZip(frameIds, uncompress, archiveRoot, archiveToken) {
  const CHUNK_SIZE = 500;
  for (let i = 0; i < frameIds.length; i += CHUNK_SIZE) {
    const chunk = frameIds.slice(i, i + CHUNK_SIZE);
    const form = $('<form>', {
      method: 'POST',
      action: `${archiveRoot}zip/`,
      target: '_blank'
    });
    chunk.forEach(function(value, j) {
      form.append($('<input>', { type: 'hidden', name: `frame_ids[${j}]`, value: value }));
    });
    form.append($('<input>', { type: 'hidden', name: 'auth_token', value: archiveToken }));
    form.append($('<input>', { type: 'hidden', name: 'uncompress', value: uncompress }));
    $('body').append(form);
    form.submit();
    form.remove();
    if (i + CHUNK_SIZE < frameIds.length) {
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
}
