//! Error types for ww2ogg conversion (vendored: the derive macro of the upstream crate is replaced by plain impls).

/// Result type alias for ww2ogg operations.
pub type WemResult<T> = Result<T, WemError>;

/// Errors that can occur during Wwise audio conversion.
#[derive(Debug)]
pub enum WemError {
    FileOpen { filename: String },
    Parse { message: String },
    SizeMismatch { expected: u64, actual: u64 },
    InvalidCodebookId { id: i32 },
    Codebook { message: String },
    Io(std::io::Error),
    EndOfStream { message: String },
}

impl std::fmt::Display for WemError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WemError::FileOpen { filename } => write!(f, "Error opening {filename}"),
            WemError::Parse { message } => write!(f, "Parse error: {message}"),
            WemError::SizeMismatch { expected, actual } => {
                write!(f, "Parse error: expected {expected} bytes, read {actual} - likely wrong codebook")
            }
            WemError::InvalidCodebookId { id } => write!(f, "Parse error: invalid codebook id {id}, try --inline-codebooks"),
            WemError::Codebook { message } => write!(f, "{message}"),
            WemError::Io(e) => write!(f, "I/O error: {e}"),
            WemError::EndOfStream { message } => write!(f, "Unexpected end of stream: {message}"),
        }
    }
}

impl std::error::Error for WemError {}

impl From<std::io::Error> for WemError {
    fn from(e: std::io::Error) -> Self {
        WemError::Io(e)
    }
}

impl WemError {
    /// Create a new parse error with the given message.
    pub fn parse(message: impl Into<String>) -> Self {
        WemError::Parse {
            message: message.into(),
        }
    }

    /// Create a new codebook error with the given message.
    pub fn codebook(message: impl Into<String>) -> Self {
        WemError::Codebook {
            message: message.into(),
        }
    }

    /// Create a new file open error.
    pub fn file_open(filename: impl Into<String>) -> Self {
        WemError::FileOpen {
            filename: filename.into(),
        }
    }

    /// Create a new size mismatch error.
    pub fn size_mismatch(expected: u64, actual: u64) -> Self {
        WemError::SizeMismatch { expected, actual }
    }

    /// Create a new invalid codebook ID error.
    pub fn invalid_codebook_id(id: i32) -> Self {
        WemError::InvalidCodebookId { id }
    }

    /// Create a new end of stream error.
    pub fn end_of_stream(message: impl Into<String>) -> Self {
        WemError::EndOfStream {
            message: message.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_error() {
        let err = WemError::parse("test message");
        assert!(matches!(err, WemError::Parse { .. }));
        assert!(err.to_string().contains("test message"));
    }

    #[test]
    fn test_codebook_error() {
        let err = WemError::codebook("codebook issue");
        assert!(matches!(err, WemError::Codebook { .. }));
        assert!(err.to_string().contains("codebook issue"));
    }

    #[test]
    fn test_file_open_error() {
        let err = WemError::file_open("test.wem");
        assert!(matches!(err, WemError::FileOpen { .. }));
        assert!(err.to_string().contains("test.wem"));
    }

    #[test]
    fn test_size_mismatch_error() {
        let err = WemError::size_mismatch(100, 50);
        assert!(matches!(err, WemError::SizeMismatch { .. }));
        let msg = err.to_string();
        assert!(msg.contains("100"));
        assert!(msg.contains("50"));
    }

    #[test]
    fn test_invalid_codebook_id_error() {
        let err = WemError::invalid_codebook_id(42);
        assert!(matches!(err, WemError::InvalidCodebookId { .. }));
        assert!(err.to_string().contains("42"));
    }

    #[test]
    fn test_end_of_stream_error() {
        let err = WemError::end_of_stream("reading header");
        assert!(matches!(err, WemError::EndOfStream { .. }));
        assert!(err.to_string().contains("reading header"));
    }

    #[test]
    fn test_io_error_from() {
        let io_err = std::io::Error::new(std::io::ErrorKind::NotFound, "file not found");
        let wem_err: WemError = io_err.into();
        assert!(matches!(wem_err, WemError::Io(_)));
    }

    #[test]
    fn test_error_display() {
        // Verify all error types implement Display correctly
        let errors: Vec<WemError> = vec![
            WemError::parse("parse"),
            WemError::codebook("codebook"),
            WemError::file_open("file"),
            WemError::size_mismatch(1, 2),
            WemError::invalid_codebook_id(1),
            WemError::end_of_stream("eos"),
        ];

        for err in errors {
            // Should not panic
            let _ = err.to_string();
        }
    }
}
