//! Session-owned observations over a shared desktop. No desktop ownership or leases.
#![allow(
    clippy::result_large_err,
    reason = "EIP error values are the protocol boundary"
)]
#![cfg_attr(
    not(target_os = "macos"),
    allow(dead_code, reason = "native backend is macOS-only")
)]

use crate::{eip::*, operation::ShortIdAllocator};
use std::{
    collections::VecDeque,
    sync::Mutex,
    time::{Duration, Instant},
};

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "macos")]
use macos as native;
#[cfg(not(target_os = "macos"))]
mod native {
    use super::*;
    pub(super) fn describe() -> Result<ComputerDescribeResult, EIPError> {
        Err(unsupported())
    }
    pub(super) fn capture(_: Option<&str>, _: u32) -> Result<Capture, EIPError> {
        Err(unsupported())
    }
    pub(super) fn execute(
        _: &Action,
        _: Option<&Basis>,
        _: &dyn Fn() -> bool,
    ) -> Result<Effect, EIPError> {
        Err(unsupported())
    }
    fn unsupported() -> EIPError {
        error(ErrorType::Unsupported, "computer use requires macOS")
    }
}

const MAX_OBSERVATIONS: usize = 16;
const OBSERVATION_TTL: Duration = Duration::from_secs(120);
pub(crate) const MAX_IMAGE_BYTES: usize = 4 * 1024 * 1024;

#[derive(Clone, Debug, PartialEq)]
struct Geometry {
    target_id: String,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

struct Capture {
    geometry: Geometry,
    width: u32,
    height: u32,
    bytes: Vec<u8>,
}

#[derive(Clone)]
struct Basis {
    observation: ComputerObservation,
    geometry: Geometry,
    created: Instant,
}

impl Basis {
    fn point(&self, point: &ComputerPoint) -> Result<(f64, f64), EIPError> {
        if point.x >= self.observation.width || point.y >= self.observation.height {
            return Err(error(
                ErrorType::InvalidParams,
                "point is outside the observation",
            ));
        }
        Ok((
            self.geometry.x
                + f64::from(point.x) * self.geometry.width / f64::from(self.observation.width),
            self.geometry.y
                + f64::from(point.y) * self.geometry.height / f64::from(self.observation.height),
        ))
    }
}

pub(crate) enum Action {
    Click(ComputerClickParams),
    Move(ComputerMoveParams),
    Drag(ComputerDragParams),
    Scroll(ComputerScrollParams),
    TypeText(ComputerTypeTextParams),
    PressKeys(ComputerPressKeysParams),
}

impl Action {
    fn observation_id(&self) -> Option<&str> {
        match self {
            Self::Click(p) => Some(&p.observation_id),
            Self::Move(p) => Some(&p.observation_id),
            Self::Drag(p) => Some(&p.observation_id),
            Self::Scroll(p) => Some(&p.observation_id),
            Self::TypeText(_) | Self::PressKeys(_) => None,
        }
    }

    fn validate(&self, basis: Option<&Basis>) -> Result<(), EIPError> {
        if let Some(basis) = basis {
            match self {
                Self::Click(p) => {
                    basis.point(&p.point)?;
                }
                Self::Move(p) => {
                    basis.point(&p.point)?;
                }
                Self::Drag(p) => {
                    basis.point(&p.start)?;
                    basis.point(&p.end)?;
                }
                Self::Scroll(p) => {
                    basis.point(&p.point)?;
                    if p.delta_x.unsigned_abs() > 10_000 || p.delta_y.unsigned_abs() > 10_000 {
                        return Err(error(
                            ErrorType::InvalidParams,
                            "scroll delta exceeds 10000 pixels",
                        ));
                    }
                }
                _ => {}
            }
        }
        match self {
            Self::TypeText(p) if p.text.is_empty() || p.text.len() > 16_384 => Err(error(
                ErrorType::InvalidParams,
                "text must contain 1 to 16384 UTF-8 bytes",
            )),
            Self::PressKeys(p)
                if p.keys.is_empty()
                    || p.keys.len() > 8
                    || p.keys.iter().any(|k| key_code(k).is_none()) =>
            {
                Err(error(
                    ErrorType::InvalidParams,
                    "keys must contain 1 to 8 supported key names",
                ))
            }
            _ => Ok(()),
        }
    }
}

pub(crate) struct Effect {
    pub(crate) effect: ComputerEffect,
    pub(crate) cleanup_complete: bool,
}

pub(crate) struct Computer {
    ids: ShortIdAllocator,
    observations: Mutex<VecDeque<Basis>>,
}

impl Computer {
    pub(crate) fn new(ids: ShortIdAllocator) -> Self {
        Self {
            ids,
            observations: Mutex::new(VecDeque::new()),
        }
    }

    pub(crate) fn describe(&self) -> Result<ComputerDescribeResult, EIPError> {
        native::describe()
    }

    pub(crate) fn observe(
        &self,
        params: &ComputerObserveParams,
    ) -> Result<(ComputerObservation, Vec<u8>), EIPError> {
        let capture = native::capture(params.target_id.as_deref(), params.max_dimension)?;
        if capture.bytes.is_empty() || capture.bytes.len() > MAX_IMAGE_BYTES {
            return Err(error(
                ErrorType::QuotaExceeded,
                "screenshot exceeds the image byte limit",
            ));
        }
        let observation = ComputerObservation {
            observation_id: self.ids.next("obs").map_err(|_| {
                error(
                    ErrorType::InternalError,
                    "observation identifier unavailable",
                )
            })?,
            target_id: capture.geometry.target_id.clone(),
            width: capture.width,
            height: capture.height,
            mime_type: "image/jpeg".to_owned(),
            captured_at: chrono::Utc::now(),
        };
        let mut observations = self
            .observations
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        observations.retain(|basis| basis.created.elapsed() < OBSERVATION_TTL);
        while observations.len() >= MAX_OBSERVATIONS {
            observations.pop_front();
        }
        observations.push_back(Basis {
            observation: observation.clone(),
            geometry: capture.geometry,
            created: Instant::now(),
        });
        Ok((observation, capture.bytes))
    }

    pub(crate) fn execute(
        &self,
        action: &Action,
        interrupted: &dyn Fn() -> bool,
    ) -> Result<Effect, EIPError> {
        let basis = if let Some(id) = action.observation_id() {
            let observations = self
                .observations
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            Some(
                observations
                    .iter()
                    .find(|b| {
                        b.observation.observation_id == id && b.created.elapsed() < OBSERVATION_TTL
                    })
                    .cloned()
                    .ok_or_else(|| {
                        error(
                            ErrorType::Conflict,
                            "observation is unavailable; capture a fresh image",
                        )
                    })?,
            )
        } else {
            None
        };
        action.validate(basis.as_ref())?;
        if interrupted() {
            return Ok(Effect {
                effect: ComputerEffect::NotExecuted,
                cleanup_complete: true,
            });
        }
        native::execute(action, basis.as_ref(), interrupted)
    }
}

// Physical ANSI key names. Text entry uses Unicode, not keyboard-layout inference.
fn key_code(key: &str) -> Option<u16> {
    Some(match key {
        "a" => 0,
        "s" => 1,
        "d" => 2,
        "f" => 3,
        "h" => 4,
        "g" => 5,
        "z" => 6,
        "x" => 7,
        "c" => 8,
        "v" => 9,
        "b" => 11,
        "q" => 12,
        "w" => 13,
        "e" => 14,
        "r" => 15,
        "y" => 16,
        "t" => 17,
        "1" => 18,
        "2" => 19,
        "3" => 20,
        "4" => 21,
        "6" => 22,
        "5" => 23,
        "=" => 24,
        "9" => 25,
        "7" => 26,
        "-" => 27,
        "8" => 28,
        "0" => 29,
        "]" => 30,
        "o" => 31,
        "u" => 32,
        "[" => 33,
        "i" => 34,
        "p" => 35,
        "enter" => 36,
        "l" => 37,
        "j" => 38,
        "'" => 39,
        "k" => 40,
        ";" => 41,
        "\\" => 42,
        "," => 43,
        "/" => 44,
        "n" => 45,
        "m" => 46,
        "." => 47,
        "tab" => 48,
        "space" => 49,
        "`" => 50,
        "backspace" => 51,
        "escape" => 53,
        "meta" => 55,
        "shift" => 56,
        "alt" => 58,
        "control" => 59,
        "f1" => 122,
        "f2" => 120,
        "f3" => 99,
        "f4" => 118,
        "f5" => 96,
        "f6" => 97,
        "f7" => 98,
        "f8" => 100,
        "f9" => 101,
        "f10" => 109,
        "f11" => 103,
        "f12" => 111,
        "home" => 115,
        "end" => 119,
        "page_up" => 116,
        "page_down" => 121,
        "delete" => 117,
        "left" => 123,
        "right" => 124,
        "down" => 125,
        "up" => 126,
        _ => return None,
    })
}

fn error(kind: ErrorType, message: &str) -> EIPError {
    crate::daemon::protocol_error(kind, message)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn basis() -> Basis {
        Basis {
            observation: ComputerObservation {
                observation_id: "obs_1".into(),
                target_id: "display_1".into(),
                width: 100,
                height: 50,
                mime_type: "image/jpeg".into(),
                captured_at: chrono::Utc::now(),
            },
            geometry: Geometry {
                target_id: "display_1".into(),
                x: -200.0,
                y: 30.0,
                width: 200.0,
                height: 100.0,
            },
            created: Instant::now(),
        }
    }

    #[test]
    fn coordinates_use_observed_pixels_and_native_origin() {
        let basis = basis();
        assert_eq!(
            basis.point(&ComputerPoint { x: 25, y: 10 }).unwrap(),
            (-150.0, 50.0)
        );
        assert!(basis.point(&ComputerPoint { x: 100, y: 0 }).is_err());
    }
    #[test]
    fn expired_and_unknown_observations_fail_before_native_dispatch() {
        let computer = Computer::new(ShortIdAllocator::for_generation(1));
        let mut expired = basis();
        expired.created = Instant::now() - OBSERVATION_TTL;
        computer.observations.lock().unwrap().push_back(expired);
        for id in ["obs_1", "obs_missing"] {
            let action = Action::Move(ComputerMoveParams {
                context: EIPCallContext {
                    operation_id: "test-input".into(),
                    timeout_ms: None,
                },
                observation_id: id.into(),
                point: ComputerPoint { x: 0, y: 0 },
            });
            let result = computer.execute(&action, &|| panic!("must reject the reference first"));
            assert!(
                matches!(result, Err(error) if error.message.contains("observation is unavailable"))
            );
        }
    }

    #[test]
    fn interrupted_input_does_not_enter_native_backend() {
        let computer = Computer::new(ShortIdAllocator::for_generation(1));
        computer.observations.lock().unwrap().push_back(basis());
        let action = Action::Move(ComputerMoveParams {
            context: EIPCallContext {
                operation_id: "test-input".into(),
                timeout_ms: None,
            },
            observation_id: "obs_1".into(),
            point: ComputerPoint { x: 99, y: 49 },
        });
        let result = computer.execute(&action, &|| true).unwrap();
        assert_eq!(result.effect, ComputerEffect::NotExecuted);
        assert!(result.cleanup_complete);
    }

    #[test]
    fn native_input_bounds_reject_oversized_unicode_keys_and_scroll() {
        for text in [String::new(), "a".repeat(16_385), "界".repeat(5_462)] {
            let action = Action::TypeText(ComputerTypeTextParams {
                context: EIPCallContext {
                    operation_id: "test-input".into(),
                    timeout_ms: None,
                },
                text,
            });
            assert!(action.validate(None).is_err());
        }
        let action = Action::TypeText(ComputerTypeTextParams {
            context: EIPCallContext {
                operation_id: "test-input".into(),
                timeout_ms: None,
            },
            text: "a".repeat(16_384),
        });
        assert!(action.validate(None).is_ok());
        for keys in [vec![], vec!["meta".into(); 9], vec!["unknown".into()]] {
            assert!(
                Action::PressKeys(ComputerPressKeysParams {
                    context: EIPCallContext {
                        operation_id: "test-input".into(),
                        timeout_ms: None,
                    },
                    keys
                })
                .validate(None)
                .is_err()
            );
        }
        for delta in [i32::MIN, -10_001, 10_001, i32::MAX] {
            let action = Action::Scroll(ComputerScrollParams {
                context: EIPCallContext {
                    operation_id: "test-input".into(),
                    timeout_ms: None,
                },
                observation_id: "obs_1".into(),
                point: ComputerPoint { x: 0, y: 0 },
                delta_x: delta,
                delta_y: 0,
            });
            assert!(action.validate(Some(&basis())).is_err());
        }
    }

    #[test]
    fn keyboard_names_are_explicit() {
        assert_eq!(key_code("meta"), Some(55));
        assert_eq!(key_code("unknown"), None);
    }
}
