/** The only share attribution kept: a source enum and a random share id, carried into the preview image URL. */
export function previewAttribution(searchParams) {
  const source = searchParams.get('s');
  const shareId = searchParams.get('sid');
  return {
    s: ['ios', 'android', 'mac', 'win', 'web'].includes(source) ? source : 'unknown',
    share_id: /^[a-zA-Z0-9_-]{8,64}$/.test(shareId || '') ? shareId : undefined,
  };
}
