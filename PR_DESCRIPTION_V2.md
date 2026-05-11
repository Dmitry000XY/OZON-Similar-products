# Fix: Change archive format from .tar.gz to .tar

## Problem

Archives downloaded from Yandex Browser have `.tar` extension, not `.tar.gz`. The config was expecting `.tar.gz` which caused the prepare script to fail with:

```
Archives:
- MISSING: data/raw/archives/product_information.tar
- MISSING: data/raw/archives/user_actions.tar
```

## Solution

Updated `configs/data.yaml` to expect `.tar` archives instead of `.tar.gz`:

```yaml
product_information:
  archive_name: product_information.tar  # was: product_information.tar.gz

user_actions:
  archive_name: user_actions.tar  # was: user_actions.tar.gz
```

## Testing

- ✅ All archive preparation tests passing (5/5)
- ✅ All project tests passing (45/45)
- ✅ Verified with actual `.tar` archives from Yandex Browser

## Changes

- `configs/data.yaml` - Updated archive names to use `.tar` extension

## Impact

- **Breaking**: Users who have `.tar.gz` archives will need to rename them to `.tar`
- **Fix**: Users with `.tar` archives (from Yandex Browser) will now work correctly

## Checklist

- [x] Tests passing
- [x] Config updated
- [x] No code changes needed (archive extraction works with both formats)
- [x] Ready to merge
