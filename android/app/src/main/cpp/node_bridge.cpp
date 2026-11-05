// Arranca Node.js (nodejs-mobile) dentro del proceso y manda su salida a logcat.
#include <android/log.h>
#include <jni.h>
#include <pthread.h>
#include <unistd.h>

#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "node.h"

namespace {

int pipeFds[2];

void* logThread(void*) {
    char buf[1024];
    ssize_t n;
    while ((n = read(pipeFds[0], buf, sizeof(buf) - 1)) > 0) {
        if (buf[n - 1] == '\n') n--;
        buf[n] = 0;
        __android_log_write(ANDROID_LOG_INFO, "AWGateway", buf);
    }
    return nullptr;
}

void redirectOutput() {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    setvbuf(stderr, nullptr, _IOLBF, 0);
    pipe(pipeFds);
    dup2(pipeFds[1], STDOUT_FILENO);
    dup2(pipeFds[1], STDERR_FILENO);
    pthread_t t;
    if (pthread_create(&t, nullptr, logThread, nullptr) == 0) pthread_detach(t);
}

}  // namespace

extern "C" JNIEXPORT jint JNICALL
Java_com_advancedwhatsapp_app_gateway_NodeBridge_startNode(JNIEnv* env, jobject, jobjectArray args) {
    redirectOutput();

    // Node exige que los argumentos estén contiguos en memoria
    jsize argc = env->GetArrayLength(args);
    std::vector<std::string> values;
    size_t total = 0;
    for (jsize i = 0; i < argc; i++) {
        auto js = (jstring) env->GetObjectArrayElement(args, i);
        const char* chars = env->GetStringUTFChars(js, nullptr);
        values.emplace_back(chars);
        total += values.back().size() + 1;
        env->ReleaseStringUTFChars(js, chars);
        env->DeleteLocalRef(js);
    }
    char* block = (char*) calloc(total, 1);
    std::vector<char*> argv(argc + 1, nullptr);
    char* cursor = block;
    for (jsize i = 0; i < argc; i++) {
        memcpy(cursor, values[i].c_str(), values[i].size());
        argv[i] = cursor;
        cursor += values[i].size() + 1;
    }
    return node::Start(argc, argv.data());
}
