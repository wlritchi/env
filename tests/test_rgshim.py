import pytest

from wlrenv.rgshim import DeliberateReplaceError, transform_args


class TestGrepStyleStripping:
    def test_bare_r_is_dropped(self) -> None:
        assert transform_args(["-r", "pattern", "path"]) == ["pattern", "path"]

    def test_rn_cluster_keeps_other_flags(self) -> None:
        assert transform_args(["-rn", "pattern"]) == ["-n", "pattern"]

    def test_nr_cluster_keeps_other_flags(self) -> None:
        assert transform_args(["-nr", "pattern"]) == ["-n", "pattern"]

    def test_rin_cluster(self) -> None:
        assert transform_args(["-rin", "pattern", "src/"]) == ["-in", "pattern", "src/"]

    def test_r_before_pattern_with_regex_anchor(self) -> None:
        # A trailing `$` is a regex anchor, not a capture reference.
        assert transform_args(["-r", "foo$", "path"]) == ["foo$", "path"]

    def test_r_as_last_argument(self) -> None:
        assert transform_args(["pattern", "-r"]) == ["pattern"]

    def test_r_after_positional(self) -> None:
        assert transform_args(["pattern", "-rn", "path"]) == ["pattern", "-n", "path"]


class TestDeliberateUseRejected:
    def test_r_with_capture_reference_value(self) -> None:
        with pytest.raises(DeliberateReplaceError):
            transform_args(["-r", "$1", "pattern"])

    def test_r_with_braced_reference_value(self) -> None:
        with pytest.raises(DeliberateReplaceError):
            transform_args(["-r", "${name}", "pattern"])

    def test_attached_value_with_capture_reference(self) -> None:
        with pytest.raises(DeliberateReplaceError):
            transform_args(["-r$1", "pattern"])

    def test_attached_value_not_a_flag_cluster(self) -> None:
        # `-rfoo`: "foo" isn't all zero-arg flags (f takes a value), so this
        # reads as replace-with-"foo" — too ambiguous to strip silently.
        with pytest.raises(DeliberateReplaceError):
            transform_args(["-rfoo", "pattern"])


class TestPassThrough:
    def test_long_form_replace_untouched(self) -> None:
        assert transform_args(["--replace", "x", "pattern"]) == [
            "--replace",
            "x",
            "pattern",
        ]

    def test_long_form_replace_equals_untouched(self) -> None:
        assert transform_args(["--replace=$1", "pattern"]) == [
            "--replace=$1",
            "pattern",
        ]

    def test_args_after_double_dash_untouched(self) -> None:
        assert transform_args(["pattern", "--", "-r", "-rn"]) == [
            "pattern",
            "--",
            "-r",
            "-rn",
        ]

    def test_r_as_value_of_short_option(self) -> None:
        # `-e` consumes the next argument, so this `-r` is a pattern.
        assert transform_args(["-e", "-r", "path"]) == ["-e", "-r", "path"]

    def test_r_as_value_of_long_option(self) -> None:
        assert transform_args(["--regexp", "-r", "path"]) == ["--regexp", "-r", "path"]

    def test_r_inside_attached_short_value(self) -> None:
        # `-t` consumes the rest of the cluster as its value.
        assert transform_args(["-trust", "pattern"]) == ["-trust", "pattern"]

    def test_no_r_at_all(self) -> None:
        args = ["-n", "-t", "py", "pattern", "src/"]
        assert transform_args(args) == args

    def test_unknown_flag_passes_through(self) -> None:
        assert transform_args(["-Z", "pattern"]) == ["-Z", "pattern"]

    def test_single_dash_positional(self) -> None:
        assert transform_args(["pattern", "-"]) == ["pattern", "-"]
