import assert from 'node:assert/strict';
import test from 'node:test';
import { previewAttribution } from '../lib/share-preview-attribution.mjs';

test('allows only source enum and random share id', () => {
  assert.deepEqual(previewAttribution(new URLSearchParams('s=ios&sid=abc12345_X')), {
    s: 'ios',
    share_id: 'abc12345_X',
  });
  assert.deepEqual(
    previewAttribution(new URLSearchParams('s=email@example.com&sid=email@example.com')),
    {
      s: 'unknown',
      share_id: undefined,
    },
  );
});
