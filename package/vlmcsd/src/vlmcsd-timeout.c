/* SPDX-License-Identifier: Apache-2.0
 * Small process-group timeout for OpenWrt and the independent recovery bundle.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t interrupted;
static void on_signal(int sig) { interrupted = sig; }
static double now(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) { perror("clock_gettime"); exit(125); }
    return ts.tv_sec + ts.tv_nsec / 1000000000.0;
}
static int number(const char *text) {
    char *end;
    errno = 0;
    long value = strtol(text, &end, 10);
    if (errno || !*text || *end || value < 0 || value > 86400) return -1;
    return (int)value;
}
int main(int argc, char **argv) {
    int index = 1, grace = 1, duration, status = 0, expired = 0, terminating = 0;
    double deadline;
    pid_t child, result;
    struct sigaction sa = {0};
    struct timespec pause = { .tv_sec = 0, .tv_nsec = 20000000 };
    if (argc > 3 && !strcmp(argv[1], "-k")) {
        grace = number(argv[2]); index = 3;
    }
    if (argc <= index + 1 || (duration = number(argv[index])) <= 0 || grace < 0 || grace > 5) {
        fprintf(stderr, "usage: vlmcsd-timeout [-k 0..5] SECONDS COMMAND [ARG...]\n"); return 125;
    }
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL); sigaction(SIGINT, &sa, NULL); sigaction(SIGHUP, &sa, NULL);
    child = fork();
    if (child < 0) { perror("fork"); return 125; }
    if (!child) {
        setpgid(0, 0);
        signal(SIGTERM, SIG_DFL); signal(SIGINT, SIG_DFL); signal(SIGHUP, SIG_DFL);
        execvp(argv[index + 1], &argv[index + 1]);
        int failure = errno;
        perror("execvp"); _exit(failure == ENOENT ? 127 : 126);
    }
    if (setpgid(child, child) && errno != EACCES && errno != ESRCH) {
        perror("setpgid"); kill(child, SIGKILL); waitpid(child, NULL, 0); return 125;
    }
    deadline = now() + duration;
    for (;;) {
        result = waitpid(child, &status, WNOHANG);
        if (result == child) break;
        if (result < 0 && errno != EINTR) { perror("waitpid"); return 125; }
        if (!terminating && (interrupted || now() >= deadline)) {
            expired = !interrupted;
            kill(-child, SIGTERM);
            terminating = 1;
            deadline = now() + grace;
        } else if (terminating && now() >= deadline) {
            kill(-child, SIGKILL);
            while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
            break;
        }
        nanosleep(&pause, NULL);
    }
    /* On cancellation, also kill descendants if the group leader exited first. */
    if (terminating) kill(-child, SIGKILL);
    if (expired) return 124;
    if (interrupted) return 128 + interrupted;
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return WIFSIGNALED(status) ? 128 + WTERMSIG(status) : 125;
}
