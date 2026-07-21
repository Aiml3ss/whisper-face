// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ParrotASRHelper",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(
            url: "https://github.com/FluidInference/FluidAudio",
            exact: "0.15.5"
        ),
    ],
    targets: [
        .executableTarget(
            name: "parrot-asr-helper",
            dependencies: [
                .product(name: "FluidAudio", package: "FluidAudio"),
            ]
        ),
    ]
)
