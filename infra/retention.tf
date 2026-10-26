# Export writes and their object-key prefix are defined in app/storage_client.py.
resource "aws_s3_bucket" "report_exports" {
  bucket = "bandwidth-static-demo-report-exports"
}

resource "aws_s3_bucket_lifecycle_configuration" "report_exports" {
  bucket = aws_s3_bucket.report_exports.id

  rule {
    id     = "expire-report-exports"
    status = "Enabled"

    filter {
      prefix = "exports/"
    }

    expiration {
      days = 3650
    }
  }
}
