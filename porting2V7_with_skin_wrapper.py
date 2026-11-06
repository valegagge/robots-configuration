#!/usr/bin/env python3

import re
from pathlib import Path


# Limit all motor, mechanical and wrapper migrations to this robot.
ROOT = Path(__file__).resolve().parent / "ergoCubSN001"

CONTROL_LAWS = {
    "positionControl": "minjerk",
    "velocityControl": "minjerk",
    "mixedControl": "minjerk",
    "torqueControl": "torque",
    "currentControl": "low_lev_current",
    "positionDirect": "direct",
    "velocityDirect": "direct",
}

EXCLUDED_TOP_LEVEL_DIRS = {
    ".git",
    ".github",
    "experimentalSetup",
    "experimentalSetups",
    "robots-icebox",
    "iCubTemplates",
    "tests",
}


def rename_param(text: str, old_name: str, new_name: str):
    pattern = re.compile(
        r'(<param\b[^>]*\bname\s*=\s*)(["\'])'
        + re.escape(old_name)
        + r'(\2)'
    )

    return pattern.subn(
        lambda m: (
            f"{m.group(1)}"
            f"{m.group(2)}"
            f"{new_name}"
            f"{m.group(3)}"
        ),
        text,
    )


def set_param_value(text: str, param_name: str, new_value: str):
    pattern = re.compile(
        r'(<param\b[^>]*\bname\s*=\s*["\']'
        + re.escape(param_name)
        + r'["\'][^>]*>)(.*?)(</param>)',
        re.MULTILINE | re.DOTALL,
    )

    count = 0

    def replacement(match):
        nonlocal count

        body = match.group(2)

        if "<" in body or ">" in body:
            return match.group(0)

        leading = re.match(r"\s*", body).group(0)
        trailing = re.search(r"\s*$", body).group(0)

        # Do not count/write when the value is already correct.
        current_value = body.strip()
        if current_value == new_value:
            return match.group(0)

        count += 1

        return (
            f"{match.group(1)}"
            f"{leading}{new_value}{trailing}"
            f"{match.group(3)}"
        )

    return pattern.sub(replacement, text), count


def convert_temperature_sensor(text: str):
    # Rename the old parameter if still present.
    text, renamed = rename_param(
        text,
        "HasTempSensor",
        "TemperatureSensorType",
    )

    pattern = re.compile(
        r'(<param\b[^>]*\bname\s*=\s*["\']TemperatureSensorType["\'][^>]*>)'
        r'(.*?)'
        r'(</param>)',
        re.MULTILINE | re.DOTALL,
    )

    replacements = 0

    def replacement(match):
        nonlocal replacements

        body = match.group(2)

        # Replace only standalone zero values.
        new_body = re.sub(
            r"(?<!\S)0(?!\S)",
            "none",
            body,
        )

        if new_body != body:
            replacements += 1

        return (
            f"{match.group(1)}"
            f"{new_body}"
            f"{match.group(3)}"
        )

    text = pattern.sub(replacement, text)

    return text, max(renamed, replacements)


def has_param(group: str, name: str):
    return re.search(
        r'<param\b[^>]*\bname\s*=\s*["\']'
        + re.escape(name)
        + r'["\']',
        group,
    ) is not None


def update_position_direct(group: str):
    """
    Add positionDirect with POS_DIR_DEFAULT for every joint when absent.

    The number of entries is derived from positionControl.

    - If positionDirect does not exist, add it immediately after
      positionControl.
    - Preserve existing positionDirect selections and their tuned PID groups.
    """

    position_control_pattern = re.compile(
        r"""
        (?P<indent>^[ \t]*)
        <param\b
        (?P<attrs>
            [^>]*\bname\s*=\s*["']positionControl["'][^>]*
        )
        >
        (?P<body>.*?)
        </param>
        """,
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    control_match = position_control_pattern.search(group)

    if not control_match:
        return group, False, False

    control_body = control_match.group("body")

    if "<" in control_body or ">" in control_body:
        return group, False, False

    control_values = control_body.split()

    if not control_values:
        return group, False, False

    direct_values = ["POS_DIR_DEFAULT"] * len(control_values)
    position_direct_values = " ".join(direct_values)

    position_direct_pattern = re.compile(
        r"""
        (?P<open>
            <param\b
            [^>]*\bname\s*=\s*["']positionDirect["'][^>]*
            >
        )
        (?P<body>.*?)
        (?P<close></param>)
        """,
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    direct_match = position_direct_pattern.search(group)

    if direct_match:
        return group, False, False

    indent = control_match.group("indent")

    new_param = (
        f'\n{indent}'
        f'<param name="positionDirect"> '
        f'{position_direct_values} '
        f'</param>'
    )

    insert_at = control_match.end()

    group = (
        group[:insert_at]
        + new_param
        + group[insert_at:]
    )

    return group, True, False


def get_position_control_template(text: str):
    """
    Return the control group referenced by positionControl.

    Normally the same group is used for all joints. If multiple different
    groups are present, use the first one and emit a warning.
    """

    controls_pattern = re.compile(
        r"""
        <group\b
        (?=[^>]*\bname\s*=\s*["'](?:CONTROLS|controls|CONTROL|control)["'])
        [^>]*>
        .*?
        </group>
        """,
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    param_pattern = re.compile(
        r"""
        <param\b
        [^>]*\bname\s*=\s*["']positionControl["'][^>]*
        >
        (?P<body>.*?)
        </param>
        """,
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    controls_match = controls_pattern.search(text)

    if not controls_match:
        return None

    param_match = param_pattern.search(controls_match.group(0))

    if not param_match:
        return None

    values = param_match.group("body").split()

    if not values:
        return None

    unique_values = []
    for value in values:
        if value.lower() != "none" and value not in unique_values:
            unique_values.append(value)

    if not unique_values:
        return None

    if len(unique_values) > 1:
        print(
            "  WARNING: positionControl references multiple groups "
            f"({', '.join(unique_values)}); using {unique_values[0]} "
            "as template for POS_DIR_DEFAULT."
        )

    return unique_values[0]


def ensure_pos_dir_default(text: str):
    """
    Ensure that the XML contains a POS_DIR_DEFAULT group.

    POS_DIR_DEFAULT is cloned from the group referenced by positionControl.
    The clone is identical except that controlLaw is set to 'direct'.

    If POS_DIR_DEFAULT already exists, only its controlLaw is forced to
    'direct', so the operation remains idempotent.
    """

    template_name = get_position_control_template(text)

    if not template_name:
        return text, False, False

    group_pattern_template = r"""
        (?P<indent>^[ \t]*)
        <group\b
        (?=[^>]*\bname\s*=\s*["']{name}["'])
        [^>]*>
        .*?
        </group>
    """

    pos_dir_pattern = re.compile(
        group_pattern_template.format(name=re.escape("POS_DIR_DEFAULT")),
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    control_law_pattern = re.compile(
        r"""
        (?P<open>
            <param\b
            [^>]*\bname\s*=\s*["']controlLaw["'][^>]*
            >
        )
        (?P<body>.*?)
        (?P<close></param>)
        """,
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    existing = pos_dir_pattern.search(text)

    if existing:
        group = existing.group(0)
        law_match = control_law_pattern.search(group)

        if not law_match:
            print(
                "  WARNING: POS_DIR_DEFAULT exists but has no controlLaw; "
                "leaving it unchanged."
            )
            return text, False, False

        if law_match.group("body").strip().lower() == "direct":
            return text, False, False

        leading = re.match(r"\s*", law_match.group("body")).group(0)
        trailing = re.search(r"\s*$", law_match.group("body")).group(0)

        new_law = (
            law_match.group("open")
            + leading
            + "direct"
            + trailing
            + law_match.group("close")
        )

        new_group = (
            group[:law_match.start()]
            + new_law
            + group[law_match.end():]
        )

        text = (
            text[:existing.start()]
            + new_group
            + text[existing.end():]
        )

        return text, False, True

    template_pattern = re.compile(
        group_pattern_template.format(name=re.escape(template_name)),
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    template_match = template_pattern.search(text)

    if not template_match:
        print(
            f"  WARNING: cannot find control group '{template_name}' "
            "referenced by positionControl; POS_DIR_DEFAULT was not created."
        )
        return text, False, False

    template_group = template_match.group(0)

    cloned_group, renamed = re.subn(
        r'(\bname\s*=\s*)(["\'])'
        + re.escape(template_name)
        + r'(\2)',
        lambda m: (
            f"{m.group(1)}{m.group(2)}POS_DIR_DEFAULT{m.group(3)}"
        ),
        template_group,
        count=1,
    )

    if renamed == 0:
        print(
            f"  WARNING: unable to rename group '{template_name}' "
            "to POS_DIR_DEFAULT."
        )
        return text, False, False

    law_match = control_law_pattern.search(cloned_group)

    if not law_match:
        print(
            f"  WARNING: control group '{template_name}' has no controlLaw; "
            "POS_DIR_DEFAULT was not created."
        )
        return text, False, False

    leading = re.match(r"\s*", law_match.group("body")).group(0)
    trailing = re.search(r"\s*$", law_match.group("body")).group(0)

    new_law = (
        law_match.group("open")
        + leading
        + "direct"
        + trailing
        + law_match.group("close")
    )

    cloned_group = (
        cloned_group[:law_match.start()]
        + new_law
        + cloned_group[law_match.end():]
    )

    text = (
        text[:template_match.end()]
        + "\n"
        + cloned_group
        + text[template_match.end():]
    )

    return text, True, False

def update_controls_group(group: str):
    stats = {
        "currentPid": 0,
        "speedPid": 0,
        "posDirectControl": 0,
        "positionDirectAdded": 0,
        "positionDirectUpdated": 0,
        "mandatoryControlsAdded": 0,
    }

    # currentPid -> currentControl
    group, count = rename_param(
        group,
        "currentPid",
        "currentControl",
    )
    stats["currentPid"] += count

    # Accept the capitalized spelling too, if present.
    group, count = rename_param(
        group,
        "CurrentPid",
        "currentControl",
    )
    stats["currentPid"] += count

    # speedPid -> velocityDirect
    group, count = rename_param(
        group,
        "speedPid",
        "velocityDirect",
    )
    stats["speedPid"] += count

    # posDirectControl -> positionDirect
    group, count = rename_param(
        group,
        "posDirectControl",
        "positionDirect",
    )
    stats["posDirectControl"] += count

    # Supply positionDirect defaults only when no selection exists.
    group, added, updated = update_position_direct(group)

    if added:
        stats["positionDirectAdded"] += 1

    if updated:
        stats["positionDirectUpdated"] += 1

    # NONE explicitly disables a mode; each mandatory field still needs one
    # entry per joint. Never invent PID gains for a previously absent mode.
    position_values = get_param_value(group, "positionControl")
    if not position_values:
        raise ValueError("CONTROLS requires a non-empty positionControl")
    joint_count = len(position_values.split())
    for mode in CONTROL_LAWS:
        if not has_param(group, mode):
            group = add_group_param(group, mode, " ".join(["NONE"] * joint_count))
            stats["mandatoryControlsAdded"] += 1
        values = get_param_value(group, mode)
        if not values or len(values.split()) != joint_count:
            raise ValueError(f"{mode} must contain {joint_count} entries")

    return group, stats


def get_param_value(group: str, name: str):
    match = re.search(
        r'<param\b[^>]*\bname\s*=\s*["\']' + re.escape(name)
        + r'["\'][^>]*>(.*?)</param>', group, re.DOTALL,
    )
    return match.group(1).strip() if match else None


def add_group_param(group: str, name: str, value: str):
    """Insert a parameter without reserializing XML or changing PID gains."""
    opening = re.search(r'<group\b[^>]*>', group)
    indent_match = re.search(r'^[ \t]*<param\b', group, re.MULTILINE)
    indent = indent_match.group(0).split("<")[0] if indent_match else "        "
    insert = f'\n{indent}<param name="{name}"> {value} </param>'
    return group[:opening.end()] + insert + group[opening.end():]


def update_pid_groups(text: str):
    """Complete all PID groups, including unused default/alternative PIDs.

    Laws follow embObjMotionControlDefaults.h. Preserve explicit output types;
    legacy PID output defaults to pwm. Remove obsolete fields only in torque
    groups, keeping position PID stiction compensation intact.
    """
    stats = {
        "controlLawAdded": 0,
        "outputTypeAdded": 0,
        "velocityDirectControlLawUpdated": 0,
        "torqueFieldsRemoved": 0,
    }
    group_pattern = re.compile(
        r'<group\b[^>]*\bname\s*=\s*["\'](?P<name>[^"\']+)["\'][^>]*>'
        r'(?:(?!<group\b).)*?</group>', re.DOTALL,
    )
    roles = {}
    for match in group_pattern.finditer(text):
        if match.group("name").upper() not in {"CONTROL", "CONTROLS"}:
            continue
        for mode, law in CONTROL_LAWS.items():
            for name in (get_param_value(match.group(0), mode) or "").split():
                if name.lower() != "none":
                    roles.setdefault(name, set()).add(law)

    def replacement(match):
        group = match.group(0)
        name = match.group("name")
        law = get_param_value(group, "controlLaw")
        if name not in roles and law is None and not has_param(group, "kp"):
            return group
        expected = roles.get(name, set())
        if len(expected) > 1:
            raise ValueError(f"PID group {name} is shared by incompatible control laws: {expected}")
        expected_law = next(iter(expected), None)
        # Unreferenced defaults still need explicit metadata.
        if expected_law is None:
            upper_name = name.upper()
            if "TRQ" in upper_name or "TORQUE" in upper_name:
                expected_law = "torque"
            elif "CUR" in upper_name:
                expected_law = "low_lev_current"
            elif "DIR" in upper_name or "VEL" in upper_name or "SPEED" in upper_name:
                expected_law = "direct"
            elif "POS" in upper_name:
                expected_law = "minjerk"
        if not law:
            if expected_law is None:
                raise ValueError(f"Cannot infer controlLaw for PID group {name}")
            if has_param(group, "controlLaw"):
                group, _ = set_param_value(group, "controlLaw", expected_law)
            else:
                group = add_group_param(group, "controlLaw", expected_law)
            law = expected_law
            stats["controlLawAdded"] += 1
        elif expected_law == "direct" or law.lower() in {"low_lev_speed", "lowlevelspeed", "low_lev_velocity"}:
            group, changed = set_param_value(group, "controlLaw", "direct")
            stats["velocityDirectControlLawUpdated"] += changed
            law = "direct"
        if not get_param_value(group, "outputType"):
            if has_param(group, "outputType"):
                group, _ = set_param_value(group, "outputType", "pwm")
            else:
                group = add_group_param(group, "outputType", "pwm")
            stats["outputTypeAdded"] += 1
        if law.lower() == "torque":
            group, removed = re.subn(
                r'[ \t]*<param\b[^>]*\bname\s*=\s*["\']'
                r'(?:bemf|stiction_up|stiction_down|stictionUp|stictionDown)\s*'
                r'["\'][^>]*>.*?</param>[ \t]*(?:\r?\n)?',
                '', group, flags=re.DOTALL,
            )
            stats["torqueFieldsRemoved"] += removed
        return group

    return group_pattern.sub(replacement, text), stats


def update_output_control_units(text: str):
    """
    Replace outputControlUnits=dutycycle_percent with machine_units.

    Other existing outputControlUnits values are left unchanged.
    The operation is idempotent.
    """

    pattern = re.compile(
        r"""
        (?P<open>
            <param\b
            [^>]*\bname\s*=\s*["']outputControlUnits["'][^>]*
            >
        )
        (?P<body>.*?)
        (?P<close></param>)
        """,
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    updated = 0

    def replacement(match):
        nonlocal updated

        body = match.group("body")

        if body.strip() != "dutycycle_percent":
            return match.group(0)

        leading = re.match(r"\s*", body).group(0)
        trailing = re.search(r"\s*$", body).group(0)

        updated += 1

        return (
            match.group("open")
            + leading
            + "machine_units"
            + trailing
            + match.group("close")
        )

    return pattern.sub(replacement, text), updated


def convert_analog_server(text: str):
    """
    Convert legacy analogServer device blocks to multipleanalogsensorsserver.

    Changes:
      - type="analogServer" -> type="multipleanalogsensorsserver"
      - *_as_wrapper* -> *_mas_wrapper*
      - startup attach level -> 10
      - shutdown detach level -> 15
      - append "_remapper" to values in paramlist name="networks"

    The operation is idempotent.
    """

    device_pattern = re.compile(
        r"""
        <device\b
        (?=[^>]*\btype\s*=\s*["']analogServer["'])
        [^>]*>
        .*?
        </device>
        """,
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    converted = 0

    def convert_device(match):
        nonlocal converted
        device = match.group(0)

        device, n = re.subn(
            r'(\btype\s*=\s*)(["\'])analogServer(\2)',
            lambda m: f'{m.group(1)}{m.group(2)}multipleanalogsensorsserver{m.group(3)}',
            device,
            count=1,
        )
        if n == 0:
            return match.group(0)

        device = re.sub(
            r'(\bname\s*=\s*["\'][^"\']*)_as_wrapper',
            r'\1_mas_wrapper',
            device,
            count=1,
        )

        device = re.sub(
            r'(<action\b(?=[^>]*\bphase\s*=\s*["\']startup["\'])'
            r'(?=[^>]*\btype\s*=\s*["\']attach["\'])'
            r'[^>]*\blevel\s*=\s*["\'])'
            r'[^"\']*(["\'])',
            r'\g<1>10\2',
            device,
            count=1,
        )

        device = re.sub(
            r'(<action\b(?=[^>]*\bphase\s*=\s*["\']shutdown["\'])'
            r'(?=[^>]*\btype\s*=\s*["\']detach["\'])'
            r'[^>]*\blevel\s*=\s*["\'])'
            r'[^"\']*(["\'])',
            r'\g<1>15\2',
            device,
            count=1,
        )

        paramlist_pattern = re.compile(
            r"""
            (?P<open>
                <paramlist\b
                [^>]*\bname\s*=\s*["']networks["'][^>]*
                >
            )
            (?P<body>.*?)
            (?P<close></paramlist>)
            """,
            re.MULTILINE | re.DOTALL | re.VERBOSE,
        )

        elem_pattern = re.compile(
            r"""
            (?P<open><elem\b[^>]*>)
            (?P<body>.*?)
            (?P<close></elem>)
            """,
            re.MULTILINE | re.DOTALL | re.VERBOSE,
        )

        def update_paramlist(pm):
            def update_elem(em):
                body = em.group("body")
                value = body.strip()

                if not value or value.endswith("_remapper"):
                    return em.group(0)

                leading = re.match(r"\s*", body).group(0)
                trailing = re.search(r"\s*$", body).group(0)

                return (
                    em.group("open")
                    + leading
                    + value
                    + "_remapper"
                    + trailing
                    + em.group("close")
                )

            return (
                pm.group("open")
                + elem_pattern.sub(update_elem, pm.group("body"))
                + pm.group("close")
            )

        device = paramlist_pattern.sub(update_paramlist, device)

        converted += 1
        return device

    return device_pattern.sub(convert_device, text), converted


def convert_skin_wrapper(text: str):
    """
    Convert legacy skinWrapper device blocks to multipleanalogsensorsserver.

    Changes:
      - type="skinWrapper" -> type="multipleanalogsensorsserver"
      - *_as_wrapper* / *_skin_wrapper* names are left unchanged unless they
        contain the legacy "_as_wrapper" token, in which case it is converted
        to "_mas_wrapper"
      - startup attach level -> 10
      - shutdown detach level -> 15
      - append "_remapper" to values in paramlist name="networks"

    Existing multipleanalogsensorsserver blocks are left untouched.
    The operation is idempotent.
    """

    device_pattern = re.compile(
        r"""
        <device\b
        (?=[^>]*\btype\s*=\s*["']skinWrapper["'])
        [^>]*>
        .*?
        </device>
        """,
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    converted = 0

    def convert_device(match):
        nonlocal converted
        device = match.group(0)

        device, n = re.subn(
            r'(\btype\s*=\s*)(["\'])skinWrapper(\2)',
            lambda m: (
                f'{m.group(1)}{m.group(2)}'
                f'multipleanalogsensorsserver'
                f'{m.group(3)}'
            ),
            device,
            count=1,
        )

        if n == 0:
            return match.group(0)

        # If the legacy wrapper name follows the analogServer convention,
        # migrate it consistently.
        device = re.sub(
            r'(\bname\s*=\s*["\'][^"\']*)_as_wrapper',
            r'\1_mas_wrapper',
            device,
            count=1,
        )

        # startup/attach level -> 10
        device = re.sub(
            r'(<action\b(?=[^>]*\bphase\s*=\s*["\']startup["\'])'
            r'(?=[^>]*\btype\s*=\s*["\']attach["\'])'
            r'[^>]*\blevel\s*=\s*["\'])'
            r'[^"\']*(["\'])',
            r'\g<1>10\2',
            device,
            count=1,
        )

        # shutdown/detach level -> 15
        device = re.sub(
            r'(<action\b(?=[^>]*\bphase\s*=\s*["\']shutdown["\'])'
            r'(?=[^>]*\btype\s*=\s*["\']detach["\'])'
            r'[^>]*\blevel\s*=\s*["\'])'
            r'[^"\']*(["\'])',
            r'\g<1>15\2',
            device,
            count=1,
        )

        # Add "_remapper" to every network device value.
        paramlist_pattern = re.compile(
            r"""
            (?P<open>
                <paramlist\b
                [^>]*\bname\s*=\s*["']networks["'][^>]*
                >
            )
            (?P<body>.*?)
            (?P<close></paramlist>)
            """,
            re.MULTILINE | re.DOTALL | re.VERBOSE,
        )

        elem_pattern = re.compile(
            r"""
            (?P<open><elem\b[^>]*>)
            (?P<body>.*?)
            (?P<close></elem>)
            """,
            re.MULTILINE | re.DOTALL | re.VERBOSE,
        )

        def update_paramlist(pm):
            def update_elem(em):
                body = em.group("body")
                value = body.strip()

                if not value or value.endswith("_remapper"):
                    return em.group(0)

                leading = re.match(r"\s*", body).group(0)
                trailing = re.search(r"\s*$", body).group(0)

                return (
                    em.group("open")
                    + leading
                    + value
                    + "_remapper"
                    + trailing
                    + em.group("close")
                )

            return (
                pm.group("open")
                + elem_pattern.sub(update_elem, pm.group("body"))
                + pm.group("close")
            )

        device = paramlist_pattern.sub(update_paramlist, device)

        converted += 1
        return device

    return device_pattern.sub(convert_device, text), converted


def update_wrapper_file(path: Path):
    """
    Apply wrapper migrations to a generic robot XML file.
    """

    original = path.read_text(encoding="utf-8")
    text = original

    text, analog_converted = convert_analog_server(text)
    text, skin_converted = convert_skin_wrapper(text)

    if text == original:
        return False, analog_converted, skin_converted

    path.write_text(text, encoding="utf-8")
    return True, analog_converted, skin_converted


def is_excluded_xml(path: Path):
    relative = path.relative_to(ROOT)

    if not relative.parts:
        return True

    return relative.parts[0] in EXCLUDED_TOP_LEVEL_DIRS




def update_file(path: Path):
    original = path.read_text(encoding="utf-8")
    # Keep disabled examples and commented parameters byte-for-byte intact.
    comments = []

    def protect_comment(match):
        comments.append(match.group(0))
        return f"__V7_COMMENT_{len(comments) - 1}__"

    text = re.sub(r"<!--.*?-->", protect_comment, original, flags=re.DOTALL)

    stats = {
        "MotioncontrolVersion": 0,
        "currentPid": 0,
        "speedPid": 0,
        "posDirectControl": 0,
        "positionDirectAdded": 0,
        "positionDirectUpdated": 0,
        "mandatoryControlsAdded": 0,
        "posDirDefaultAdded": 0,
        "posDirDefaultUpdated": 0,
        "velocityDirectControlLawUpdated": 0,
        "outputTypeAdded": 0,
        "controlLawAdded": 0,
        "torqueFieldsRemoved": 0,
        "outputControlUnitsUpdated": 0,
        "HasTempSensor": 0,
    }

    controls_pattern = re.compile(
        r'''
        <group\b
        (?=[^>]*\bname\s*=\s*["'](?:CONTROLS|controls|CONTROL|control)["'])
        [^>]*>
        .*?
        </group>
        ''',
        re.MULTILINE | re.DOTALL | re.VERBOSE,
    )

    def controls_replacement(match):
        group = match.group(0)

        new_group, group_stats = update_controls_group(group)

        for key in group_stats:
            stats[key] += group_stats[key]

        return new_group

    text = controls_pattern.sub(
        controls_replacement,
        text,
    )

    text, pid_stats = update_pid_groups(text)
    for key, count in pid_stats.items():
        stats[key] += count

    # Create/update POS_DIR_DEFAULT from the group referenced by
    # positionControl, changing only controlLaw to "direct".
    text, pos_dir_added, pos_dir_updated = ensure_pos_dir_default(text)

    if pos_dir_added:
        stats["posDirDefaultAdded"] += 1

    if pos_dir_updated:
        stats["posDirDefaultUpdated"] += 1

    # Version 7 uses machine_units instead of dutycycle_percent.
    text, output_units_updated = update_output_control_units(text)
    stats["outputControlUnitsUpdated"] += output_units_updated

    for index, comment in enumerate(comments):
        text = text.replace(f"__V7_COMMENT_{index}__", comment)

    if text == original:
        return False, stats

    path.write_text(
        text,
        encoding="utf-8",
    )

    return True, stats


def should_process_mechanical(path: Path):
    """
    Process only mechanical configuration XML files:
        <robot>/hardware/mechanicals/*-mec.xml
    """
    relative = path.relative_to(ROOT)

    if not relative.parts:
        return False

    if relative.parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
        return False

    return (
        "hardware" in relative.parts
        and "mechanicals" in relative.parts
        and path.name.endswith("-mec.xml")
    )


def update_mechanical_file(path: Path):
    """
    Apply version-7 migrations that belong to mechanical files only:
      - MotioncontrolVersion -> 7
      - HasTempSensor -> TemperatureSensorType
      - standalone 0 values in TemperatureSensorType -> none
    """
    original = path.read_text(encoding="utf-8")
    text = original

    stats = {
        "MotioncontrolVersion": 0,
        "HasTempSensor": 0,
    }

    text, stats["MotioncontrolVersion"] = set_param_value(
        text,
        "MotioncontrolVersion",
        "7",
    )

    text, stats["HasTempSensor"] = convert_temperature_sensor(text)

    if text == original:
        return False, stats

    path.write_text(text, encoding="utf-8")
    return True, stats


def should_process(path: Path):
    relative = path.relative_to(ROOT)

    if not relative.parts:
        return False

    if relative.parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
        return False

    # EmbObj motor PID schema does not apply to JointTorqueControlDevice wrappers.
    return (
        "hardware" in relative.parts
        and "motorControl" in relative.parts
        and path.name.endswith("-mc.xml")
    )


def main():
    if not ROOT.is_dir():
        raise FileNotFoundError(f"Robot configuration directory does not exist: {ROOT}")
    totals = {
        "MotioncontrolVersion": 0,
        "currentPid": 0,
        "speedPid": 0,
        "posDirectControl": 0,
        "positionDirectAdded": 0,
        "positionDirectUpdated": 0,
        "mandatoryControlsAdded": 0,
        "posDirDefaultAdded": 0,
        "posDirDefaultUpdated": 0,
        "velocityDirectControlLawUpdated": 0,
        "outputTypeAdded": 0,
        "controlLawAdded": 0,
        "torqueFieldsRemoved": 0,
        "outputControlUnitsUpdated": 0,
        "analogServerConverted": 0,
        "skinWrapperConverted": 0,
        "HasTempSensor": 0,
    }

    modified_files = 0

    modified_paths = set()

    for xml_file in sorted(ROOT.rglob("*.xml")):
        if is_excluded_xml(xml_file):
            continue

        # Mechanical configuration migration.
        if should_process_mechanical(xml_file):
            changed, mechanical_stats = update_mechanical_file(xml_file)

            totals["MotioncontrolVersion"] += mechanical_stats["MotioncontrolVersion"]
            totals["HasTempSensor"] += mechanical_stats["HasTempSensor"]

            if changed:
                modified_paths.add(xml_file)
                print(
                    f"MODIFIED (mechanicals): "
                    f"{xml_file.relative_to(ROOT)}"
                )

        # Motor-control PID migration.
        if should_process(xml_file):
            changed, stats = update_file(xml_file)

            for key in stats:
                totals[key] += stats[key]

            if changed:
                modified_paths.add(xml_file)
                print(
                    f"MODIFIED (motorControl): "
                    f"{xml_file.relative_to(ROOT)}"
                )

        changed, analog_converted, skin_converted = update_wrapper_file(xml_file)
        totals["analogServerConverted"] += analog_converted
        totals["skinWrapperConverted"] += skin_converted

        if changed:
            if xml_file not in modified_paths:
                print(
                    f"MODIFIED (wrapper): "
                    f"{xml_file.relative_to(ROOT)}"
                )
            modified_paths.add(xml_file)

    modified_files = len(modified_paths)

    print()
    print("========================================")
    print("Motion-control migration summary")
    print("========================================")
    print(f"Modified files:                 {modified_files}")
    print(
        f"MotioncontrolVersion -> 7:      "
        f"{totals['MotioncontrolVersion']}"
    )
    print(
        f"currentPid -> currentControl:   "
        f"{totals['currentPid']}"
    )
    print(
        f"speedPid -> velocityDirect:     "
        f"{totals['speedPid']}"
    )
    print(
        f"posDirectControl renamed:       "
        f"{totals['posDirectControl']}"
    )
    print(
        f"positionDirect added:           "
        f"{totals['positionDirectAdded']}"
    )
    print(
        f"positionDirect updated:         "
        f"{totals['positionDirectUpdated']}"
    )
    print(
        f"POS_DIR_DEFAULT added:          "
        f"{totals['posDirDefaultAdded']}"
    )
    print(
        f"POS_DIR_DEFAULT updated:        "
        f"{totals['posDirDefaultUpdated']}"
    )
    print(
        f"velocityDirect controlLaw fixed:"
        f" {totals['velocityDirectControlLawUpdated']}"
    )
    print(
        f"outputType added:               "
        f"{totals['outputTypeAdded']}"
    )
    print(
        f"outputControlUnits updated:     "
        f"{totals['outputControlUnitsUpdated']}"
    )
    print(f"Mandatory controls added:       {totals['mandatoryControlsAdded']}")
    print(f"controlLaw added:               {totals['controlLawAdded']}")
    print(f"Obsolete torque fields removed: {totals['torqueFieldsRemoved']}")
    print(
        f"analogServer converted:         "
        f"{totals['analogServerConverted']}"
    )
    print(
        f"skinWrapper converted:          "
        f"{totals['skinWrapperConverted']}"
    )
    print(
        f"HasTempSensor converted:        "
        f"{totals['HasTempSensor']}"
    )


if __name__ == "__main__":
    main()
