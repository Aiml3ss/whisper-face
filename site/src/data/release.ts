// The current downloadable build. Update these four values when cutting a
// release; every download link on the site reads from here.
export const RELEASE = {
  version: '0.3.0',
  tag: 'v0.3.0',
  /** Apple Silicon disk image. */
  dmg: 'https://github.com/Aiml3ss/whisper-face/releases/download/v0.3.0/WhisperFace-0.3.0-macOS-arm64.dmg',
  size: '13.4 MB',
  notes: 'https://github.com/Aiml3ss/whisper-face/releases/tag/v0.3.0',
  /** Windows 10/11 x64 source bundle; unzip and run Install.bat. */
  windows:
    'https://github.com/Aiml3ss/whisper-face/releases/download/v0.3.0/WhisperFace-0.3.0-windows-x64.zip',
  windowsSize: 'TBD',
  checksums:
    'https://github.com/Aiml3ss/whisper-face/releases/download/v0.3.0/SHA256SUMS',
  /**
   * Ad-hoc signed, not notarized: the project is not yet enrolled in the Apple
   * Developer Program, so macOS will not vouch for the developer. Flip this to
   * false once a Developer ID build ships and the Gatekeeper note can go.
   */
  unsigned: true,
  /**
   * Windows shares the pipeline and its installer runs in CI on every
   * change, but no full dictation has been performed on real Windows
   * hardware yet. Flip this to false once that has actually happened --
   * not once it is expected to work.
   */
  windowsPreview: true,
} as const;
