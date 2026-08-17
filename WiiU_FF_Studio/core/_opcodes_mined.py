"""T6 opcode -> mnemonic map.

Derived by aligning this project's disassembler (`core.gsc`, which knows the opcode BYTE at
every address) against gsc-tool's disassembly (which prints mnemonics but not bytes). Zipping
the two instruction streams yields the mapping, and the method is self-validating: a bad
alignment contradicts itself almost immediately.

Provenance:
  * 64 entries from a corpus run -- 172/185 scripts, 319,570 instructions, conflict-free.
  * 15 further entries (the arithmetic/comparison/bitwise block, Return, the pointer-call and
    Notify/VoidCodePos forms) from a purpose-written source exercising every operator, aligned
    144/144 against gsc-tool, conflict-free.

2026-08-13: 14 further entries (the dvar / vector-math intrinsic block, GetGame,
GetAnimObject and WaitTillFrameEnd) mined the same way but aligned PER FUNCTION against
gsc-tool -- 711 functions, ZERO conflicting votes. Aligning per function rather than per file
is what made them reachable: a script whose overall instruction count disagrees still
contributes every function that agrees.

⚠ CHECKED IN DELIBERATELY, not regenerated on demand. `core/_mine_opcodes.py` OVERWRITES this
file, and a run whose walker is mid-change can silently replace a good table with a worse one
-- that happened once here (79 entries clobbered by a 30-entry run). If you re-mine, diff the
result against this before accepting it.
"""
MNEMONIC = {
    0x00: 'End',
    0x01: 'Return',
    0x02: 'GetUndefined',
    0x03: 'GetZero',
    0x04: 'GetByte',
    0x05: 'GetNegByte',
    0x06: 'GetUnsignedShort',
    0x07: 'GetNegUnsignedShort',
    0x08: 'GetInteger',
    0x09: 'GetFloat',
    0x0A: 'GetString',
    0x0B: 'GetIString',
    0x0D: 'GetLevelObject',
    0x0F: 'GetSelf',
    0x10: 'GetLevel',
    0x13: 'GetAnimation',
    0x14: 'GetGameRef',
    0x15: 'GetFunction',
    0x17: 'SafeCreateLocalVariables',
    0x19: 'EvalLocalVariableCached',
    0x1A: 'EvalArray',
    0x1C: 'EvalArrayRef',
    0x1E: 'EmptyArray',
    0x1F: 'GetSelfObject',
    0x20: 'EvalFieldVariable',
    0x21: 'EvalFieldVariableRef',
    0x24: 'SafeSetWaittillVariableFieldCached',
    0x25: 'ClearParams',
    0x26: 'CheckClearParams',
    0x27: 'EvalLocalVariableRefCached',
    0x28: 'SetVariableField',
    0x2B: 'Wait',
    0x2D: 'PreScriptCall',
    0x2E: 'ScriptFunctionCall',
    0x2F: 'ScriptFunctionCallPointer',
    0x30: 'ScriptMethodCall',
    0x32: 'ScriptThreadCall',
    0x34: 'ScriptMethodThreadCall',
    0x36: 'DecTop',
    0x37: 'CastFieldObject',
    0x39: 'BoolNot',
    0x3A: 'BoolComplement',
    0x3B: 'JumpOnFalse',
    0x3C: 'JumpOnTrue',
    0x3D: 'JumpOnFalseExpr',
    0x3E: 'JumpOnTrueExpr',
    0x3F: 'Jump',
    0x41: 'Inc',
    0x42: 'Dec',
    0x43: 'Bit_Or',
    0x44: 'Bit_Xor',
    0x45: 'Bit_And',
    0x46: 'Equal',
    0x47: 'NotEqual',
    0x48: 'LessThan',
    0x49: 'GreaterThan',
    0x4A: 'LessThanOrEqualTo',
    0x4B: 'GreaterThanOrEqualTo',
    0x4C: 'ShiftLeft',
    0x4D: 'ShiftRight',
    0x4E: 'Plus',
    0x4F: 'Minus',
    0x50: 'Multiply',
    0x51: 'Divide',
    0x52: 'Modulus',
    0x53: 'SizeOf',
    0x55: 'WaitTill',
    0x56: 'Notify',
    0x57: 'EndOn',
    0x58: 'VoidCodePos',
    0x59: 'Switch',
    0x5A: 'EndSwitch',
    0x5B: 'Vector',
    0x5C: 'GetHash',
    0x5E: 'VectorConstant',
    0x5F: 'IsDefined',
    0x60: 'VectorScale',
    0x69: 'GetDvarInt',
    0x70: 'FirstArrayKey',
    0x71: 'NextArrayKey',
    0x7B: 'DevblockBegin',
    0x2C: 'WaitTillFrameEnd',
    0x61: 'AnglesToUp',
    0x63: 'AnglesToForward',
    0x64: 'AngleClamp180',
    0x65: 'VectorToAngles',
    0x66: 'Abs',
    0x67: 'GetTime',
    0x68: 'GetDvar',
    0x6A: 'GetDvarFloat',
    0x6C: 'GetDvarColorRed',
    0x6D: 'GetDvarColorGreen',
    0x6E: 'GetDvarColorBlue',
    0x0E: 'GetAnimObject',
    0x11: 'GetGame',
    0x62: 'AnglesToRight',
}
