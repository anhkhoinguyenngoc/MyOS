#include <stddef.h>
#include <stdint.h>

#include "kernel/shell.h"
#include "kernel/fb_console.h"

static const char *skip_spaces(const char *p)
{
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    return p;
}

static int token_equals(const char *p, const char *token)
{
    while (*token != '\0') {
        if (*p != *token) {
            return 0;
        }
        p++;
        token++;
    }
    return *p == '\0' || *p == ' ' || *p == '\t';
}

static void shell_help(void)
{
    console_puts("help    - show commands\n");
    console_puts("clear   - clear the screen\n");
    console_puts("echo X  - print X\n");
    console_puts("version - show version\n");
}

void shell_execute(const char *line)
{
    const char *command;
    const char *arg;

    if (line == NULL) {
        return;
    }
    command = skip_spaces(line);
    if (*command == '\0') {
        return;
    }

    if (token_equals(command, "help")) {
        shell_help();
        return;
    }
    if (token_equals(command, "clear")) {
        console_clear();
        console_puts("MyOS v0.1 - Hello User!\n");
        return;
    }
    if (token_equals(command, "version")) {
        console_puts("MyOS v0.1 (A8 raw console)\n");
        return;
    }
    if (token_equals(command, "echo")) {
        arg = skip_spaces(command + 4);
        console_puts(arg);
        console_putc('\n');
        return;
    }

    console_puts("Unknown command: ");
    while (*command != '\0' && *command != ' ' && *command != '\t') {
        console_putc(*command);
        command++;
    }
    console_putc('\n');
}

void shell_run(void)
{
    static const char *const demo[] = {
        "help",
        "version",
        "echo Hello User",
        NULL
    };

    console_puts("MyOS v0.1 - Hello User!\n");
    console_puts("Type help for commands.\n\n");

    for (uint32_t i = 0; demo[i] != NULL; i++) {
        console_puts("MyOS> ");
        console_puts(demo[i]);
        console_putc('\n');
        shell_execute(demo[i]);
        console_putc('\n');
    }
    console_puts("MyOS> ");
}
