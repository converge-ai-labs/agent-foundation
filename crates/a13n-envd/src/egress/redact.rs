//! One-pass longest-match substitution; replacements are never scanned again.

pub(super) fn replace(input: &[u8], patterns: &[(&[u8], &[u8])]) -> Vec<u8> {
    let mut output = Vec::with_capacity(input.len());
    let mut position = 0;
    while position < input.len() {
        if let Some((needle, replacement)) = patterns
            .iter()
            .filter(|(needle, _)| !needle.is_empty() && input[position..].starts_with(needle))
            .max_by_key(|(needle, _)| needle.len())
        {
            output.extend_from_slice(replacement);
            position += needle.len();
        } else {
            output.push(input[position]);
            position += 1;
        }
    }
    output
}

/// Withholds only a possible secret prefix, independent of upstream frame boundaries.
/// The caller must cap input frame size and forward output under backpressure.
pub(super) struct Scrubber {
    patterns: Vec<(Vec<u8>, Vec<u8>)>,
    pending: Vec<u8>,
}

impl Scrubber {
    pub fn new(mut patterns: Vec<(Vec<u8>, Vec<u8>)>) -> Self {
        patterns.retain(|(needle, _)| !needle.is_empty());
        patterns.sort_by_key(|pattern| std::cmp::Reverse(pattern.0.len()));
        Self {
            patterns,
            pending: Vec::new(),
        }
    }

    pub fn push(&mut self, bytes: &[u8], end: bool) -> Vec<u8> {
        self.pending.extend_from_slice(bytes);
        let mut output = Vec::new();
        let mut position = 0;
        while position < self.pending.len() {
            let remaining = &self.pending[position..];
            // Delay a shorter complete match if a longer secret may still follow.
            if !end
                && self.patterns.iter().any(|(needle, _)| {
                    needle.len() > remaining.len() && needle.starts_with(remaining)
                })
            {
                break;
            }
            if let Some((needle, replacement)) = self
                .patterns
                .iter()
                .find(|(needle, _)| remaining.starts_with(needle))
            {
                output.extend_from_slice(replacement);
                position += needle.len();
            } else {
                output.push(self.pending[position]);
                position += 1;
            }
        }
        self.pending.drain(..position);
        output
    }
}

impl Drop for Scrubber {
    fn drop(&mut self) {
        use zeroize::Zeroize;
        self.pending.zeroize();
        for (needle, _) in &mut self.patterns {
            needle.zeroize();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_chunk_boundary_preserves_longest_match_and_binary_data() {
        let input = b"\0abcXabcd\xffabc";
        let patterns = vec![
            (b"abc".to_vec(), b"short".to_vec()),
            (b"abcd".to_vec(), b"long".to_vec()),
        ];
        for first in 0..=input.len() {
            for second in first..=input.len() {
                let mut scrubber = Scrubber::new(patterns.clone());
                let mut actual = scrubber.push(&input[..first], false);
                actual.extend(scrubber.push(&input[first..second], false));
                actual.extend(scrubber.push(&input[second..], true));
                assert_eq!(actual, b"\0shortXlong\xffshort");
            }
        }
    }

    #[test]
    fn replacement_bytes_are_not_recursively_substituted() {
        assert_eq!(
            replace(
                b"first second",
                &[(b"first", b"second"), (b"second", b"third")]
            ),
            b"second third"
        );
    }
}
