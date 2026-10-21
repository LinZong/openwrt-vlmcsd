#!/usr/bin/env python3
"""Read target paths from SDK make variables rather than guessing ABI directory names."""
import json
import os
import subprocess
from pathlib import Path


def sdk_layout(sdk):
    sdk = Path(sdk).resolve()
    env = dict(os.environ, STAGING_DIR_HOST=str(sdk / 'staging_dir/host'))
    env['PATH'] = env['STAGING_DIR_HOST'] + '/bin:' + env['PATH']
    source = ('include $(TOPDIR)/rules.mk\n'
              '.PHONY: vlmcsd-layout\nvlmcsd-layout:\n'
              "\t@printf '%s\\n' '$(BUILD_DIR)' '$(TOOLCHAIN_DIR)' '$(TARGET_CROSS)'\n")
    output = subprocess.check_output(['make', '--no-print-directory', '-r', '-s', '-f', '-',
                                      'TOPDIR=' + str(sdk), 'vlmcsd-layout'],
                                     cwd=sdk, input=source, text=True, env=env)
    values = output.strip().splitlines()
    if len(values) != 3:
        raise ValueError('Unexpected SDK target layout: ' + output)
    build, toolchain, cross = values
    for path in [build, toolchain]:
        if not Path(path).resolve().is_relative_to(sdk):
            raise ValueError('SDK target path escapes selected SDK')
    return {'build_dir': build, 'toolchain_dir': toolchain, 'target_cross': cross}


if __name__ == '__main__':
    import sys
    print(json.dumps(sdk_layout(sys.argv[1]), indent=2))
