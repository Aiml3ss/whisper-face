// The current downloadable build. Update every value below when cutting a
// release; every download link on the site reads from here.
export const RELEASE = {
  version: '0.7.1',
  tag: 'v0.7.1',
  /** Apple Silicon disk image: the drag-install app and a link to Applications. */
  dmg: 'https://github.com/Aiml3ss/whisper-face/releases/download/v0.7.1/WhisperFace-0.7.1-macOS-arm64.dmg',
  // SIZE-PENDING: replace with the published disk image's size before deploying.
  size: 'SIZE-PENDING',
  notes: 'https://github.com/Aiml3ss/whisper-face/releases/tag/v0.7.1',
  /** Windows 10/11 x64 source bundle; unzip and run Install.cmd. */
  windows:
    'https://github.com/Aiml3ss/whisper-face/releases/download/v0.7.1/WhisperFace-0.7.1-windows-x64.zip',
  // SIZE-PENDING: replace with the published Windows zip's size before deploying.
  windowsSize: 'SIZE-PENDING',
  /** Checksums for the Mac assets. This file does not list the Windows zip. */
  checksums:
    'https://github.com/Aiml3ss/whisper-face/releases/download/v0.7.1/SHA256SUMS',
  /** The Windows zip's own checksum file. */
  windowsChecksums:
    'https://github.com/Aiml3ss/whisper-face/releases/download/v0.7.1/SHA256SUMS-windows',
  /**
   * From 0.7.1 the Mac app and disk image are signed with the Developer ID
   * (team Z6Q832529P), notarized by Apple and stapled, so the install steps
   * carry no Open Anyway note. Releases up to 0.7.0 were ad-hoc signed and not
   * notarized; set this back to true only for a build that is not notarized.
   */
  unsigned: false,
  /**
   * CI runs the shared Python code and tests on Windows and only parses
   * setup.ps1; neither the installer nor a full dictation has been run on
   * real Windows hardware. Flip this to false once both have actually
   * happened -- not once they are expected to work.
   */
  windowsPreview: true,
} as const;
