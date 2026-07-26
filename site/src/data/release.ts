// The current downloadable build. Update these four values when cutting a
// release; every download link on the site reads from here.
export const RELEASE = {
  version: '0.2.0',
  tag: 'v0.2.0',
  /** Apple Silicon disk image. */
  dmg: 'https://github.com/Aiml3ss/whisper-face/releases/download/v0.2.0/WhisperFace-0.2.0-macOS-arm64.dmg',
  size: '2.8 MB',
  notes: 'https://github.com/Aiml3ss/whisper-face/releases/tag/v0.2.0',
  checksums:
    'https://github.com/Aiml3ss/whisper-face/releases/download/v0.2.0/SHA256SUMS',
  /**
   * Ad-hoc signed, not notarized: the project is not yet enrolled in the Apple
   * Developer Program, so macOS will not vouch for the developer. Flip this to
   * false once a Developer ID build ships and the Gatekeeper note can go.
   */
  unsigned: true,
} as const;
