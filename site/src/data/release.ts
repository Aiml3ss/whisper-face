// The current downloadable build. Update every value below when cutting a
// release; every download link on the site reads from here.
export const RELEASE = {
  version: '0.4.0',
  tag: 'v0.4.0',
  /** Apple Silicon disk image. */
  dmg: 'https://github.com/Aiml3ss/whisper-face/releases/download/v0.4.0/WhisperFace-0.4.0-macOS-arm64.dmg',
  size: '26.2 MB',
  notes: 'https://github.com/Aiml3ss/whisper-face/releases/tag/v0.4.0',
  /** Windows 10/11 x64 source bundle; unzip and run Install.cmd. */
  windows:
    'https://github.com/Aiml3ss/whisper-face/releases/download/v0.4.0/WhisperFace-0.4.0-windows-x64.zip',
  windowsSize: '24.0 MB',
  /** Checksums for the Mac assets. This file does not list the Windows zip. */
  checksums:
    'https://github.com/Aiml3ss/whisper-face/releases/download/v0.4.0/SHA256SUMS',
  /** The Windows zip's own checksum file. */
  windowsChecksums:
    'https://github.com/Aiml3ss/whisper-face/releases/download/v0.4.0/SHA256SUMS-windows',
  /**
   * Ad-hoc signed, not notarized: the project is not yet enrolled in the Apple
   * Developer Program, so macOS will not vouch for the developer. Flip this to
   * false once a Developer ID build ships and the Gatekeeper note can go.
   */
  unsigned: true,
  /**
   * CI runs the shared Python code and tests on Windows and only parses
   * setup.ps1; neither the installer nor a full dictation has been run on
   * real Windows hardware. Flip this to false once both have actually
   * happened -- not once they are expected to work.
   */
  windowsPreview: true,
} as const;
