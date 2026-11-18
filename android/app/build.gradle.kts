plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.advancedwhatsapp.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.advancedwhatsapp.app"
        minSdk = 29          // Android 10: primera versión que graba OGG/Opus (notas de voz de WhatsApp)
        targetSdk = 34
        versionCode = 10
        versionName = "0.6.1"
        externalNativeBuild {
            cmake {
                cppFlags += "-std=c++17"
                arguments += "-DANDROID_STL=c++_shared"   // libnode.so la necesita
            }
        }
    }

    externalNativeBuild {
        cmake { path = file("src/main/cpp/CMakeLists.txt") }
    }

    // Un APK por arquitectura: Node pesa mucho y cada celular usa una sola (x86_64 es para el emulador)
    splits {
        abi {
            isEnable = true
            reset()
            include("arm64-v8a", "x86_64")
            isUniversalApk = false
        }
    }

    buildTypes {
        release {
            // Instalación directa: se firma con la clave de depuración de esta PC.
            // Compose funciona mucho más fluido que en la variante debug.
            signingConfig = signingConfigs.getByName("debug")
            // R8 optimiza el código: la app arranca más rápido y el APK pesa menos
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    testOptions { unitTests.isReturnDefaultValues = true }

    // libnode.so se empaqueta como librería nativa normal
    sourceSets["main"].jniLibs.srcDir("libnode/bin")

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    packaging {
        jniLibs { useLegacyPackaging = true }   // Node se carga más rápido descomprimido en disco
    }
}

// ─── El gateway (gateway/server.js + dependencias) viaja como un zip dentro del APK ──────────────
val gatewayDir = rootProject.projectDir.resolve("../gateway")
val gatewayOut = layout.buildDirectory.dir("generated/gateway")
val npmCmd = if (System.getProperty("os.name").lowercase().contains("win")) "npm.cmd" else "npm"

val gatewayInstall = tasks.register<Exec>("gatewayInstall") {
    description = "Instala las dependencias del gateway (solo si cambió package-lock.json)"
    workingDir = gatewayDir
    commandLine(npmCmd, "ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund")
    inputs.file(gatewayDir.resolve("package-lock.json"))
    outputs.file(gatewayDir.resolve("node_modules/.package-lock.json"))
}

val gatewayZip = tasks.register<Zip>("gatewayZip") {
    dependsOn(gatewayInstall)
    archiveFileName.set("gateway.zip")
    destinationDirectory.set(gatewayOut.map { it.dir("assets") })
    from(gatewayDir) {
        include("server.js", "extras.js", "package.json", "node_modules/**")
        exclude("**/*.md", "**/*.map", "**/*.d.ts", "**/*.d.cts", "**/*.d.mts", "**/*.ts.map",
            "**/test/**", "**/tests/**", "**/.github/**", "**/docs/**")
    }
    from("src/main/node") { include("android_main.mjs") }
}

android.sourceSets["main"].assets.srcDir(gatewayOut.map { it.dir("assets") })
tasks.named("preBuild") { dependsOn(gatewayZip) }

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.03")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.navigation:navigation-compose:2.8.2")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.biometric:biometric:1.1.0")   // huella para abrir chats bloqueados
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("io.coil-kt:coil-compose:2.7.0")
    implementation("io.coil-kt:coil-gif:2.7.0")            // stickers animados (.webp) en las burbujas
    testImplementation("junit:junit:4.13.2")
}
