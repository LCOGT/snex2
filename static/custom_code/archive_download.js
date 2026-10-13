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
