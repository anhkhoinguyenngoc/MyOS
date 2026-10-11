with open('build/audit/m2-a7/tools/run_m2a7.py', 'r') as f:
    lines = f.readlines()

# Fix lines 313-325 (0-indexed: 313-322)
# The block from "for (slot, value, off) in expected_slots:" to "entry_raw = read_mem(bias, 4)"
# should be replaced with properly indented code

new_lines = lines[:312] + [
    "                raw = read_mem(slot, 8)\n",
    "                got = struct.unpack(\"<Q\", raw)[0] if raw else None\n",
    "                ok = got == value\n",
    "                all_ok &= ok\n",
    "                if not ok or off in (0x2708, 0x31250, 0x31258):\n",
    "                    slot_results.append(\n",
    "                        dict(offset=\"%#x\" % off, slot=\"%#x\" % slot,\n",
    "                             expected=\"%#x\" % value,\n",
    "                             got=None if got is None else \"%#x\" % got),\n",
    "                        )\n",
    "            entry_raw = read_mem(bias, 4)\n",
    "            entry_word = struct.unpack(\"<I\", entry_raw)[0] \\\n",
    "                if entry_raw else None\n",
    "            # PC check\n",
    "            pc = None\n",
    "            if isinstance(registers, str):\n",
    "                m = re.search(r\"PC=([0-9a-fA-F]+)\", registers)\n",
    "                if m:\n",
    "                    pc = int(m.group(1), 16)\n",
    "                sp = None\n",
    "                if isinstance(registers, str):\n",
    "                    m = re.search(r\"SP=([0-9a-fA-F]+)\", registers)\n",
    "                    if m:\n",
    "                        sp = int(m.group(1), 16)\n",
] + lines[324:]

with open('build/audit/m2-a7/tools/run_m2a7.py', 'w') as f:
    f.writelines(lines)
print('Done')