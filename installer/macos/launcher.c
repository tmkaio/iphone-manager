/* The .app's executable: runs the bundled Python on run.py --detach, which starts the server in the
 * background (if it isn't running yet), opens the app window and returns. A native binary rather
 * than a shell script, so the bundle signs and notarizes like any other app. */
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(void) {
    char exe[PATH_MAX], real[PATH_MAX], python[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, real) == NULL) {
        fprintf(stderr, "iPhone Manager: can't find its own path\n");
        return 1;
    }
    char *macos = dirname(real); /* <app>/Contents/MacOS */
    snprintf(python, sizeof(python), "%s/../Resources/python/bin/python3", macos);
    snprintf(script, sizeof(script), "%s/../Resources/app/run.py", macos);
    execl(python, python, "-s", script, "--detach", (char *)NULL);
    perror("iPhone Manager: exec python");
    return 1;
}
