// swift-tools-version: 6.0
import PackageDescription

// Whisper Face's own Parakeet runtime. It has no package dependencies: it
// loads the pinned Core ML model files from a folder and does the rest with
// Apple frameworks (Foundation, Core ML, Accelerate). Nothing is fetched to
// build it, and the tools version matches native/ParrotASRHelper so every Mac
// that builds that helper builds this one.
let package = Package(
    name: "ParrotSpeechRuntime",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "parrot-speech-runtime",
            path: "Sources/parrot-speech-runtime"
        ),
    ]
)
