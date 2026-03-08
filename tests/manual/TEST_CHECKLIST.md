# Manual Testing Checklist

Complete checklist for manual testing of Synth Gallery functionality.

## First Run & Setup

### Initial Setup
- [ ] Delete `gallery.db` to simulate first run
- [ ] Start application (`Start.bat` or `uvicorn`)
- [ ] Verify console shows default admin account creation warning
- [ ] Navigate to login page
- [ ] Login with `admin/admin`
- [ ] Verify redirect to gallery

### Admin User Creation (First Steps)
- [ ] Go to `/admin/users`
- [ ] Click "Add User" button
- [ ] Create new user with admin rights
- [ ] Verify new user appears in list
- [ ] Delete temporary `admin` account
- [ ] Verify account deletion works
- [ ] Logout and login with new admin account

---

## Authentication & Authorization

### Login/Logout
- [ ] Login with valid credentials
- [ ] Login with invalid credentials shows error
- [ ] Logout redirects to login
- [ ] Session persists after page reload
- [ ] Session expires correctly (test with short expiry)

### Password Protection
- [ ] Go to `/settings`
- [ ] Change display name (Profile section)
- [ ] Verify name updates immediately
- [ ] Change password (Security section)
- [ ] Verify old password required
- [ ] Verify new password confirmation
- [ ] Logout and login with new password

### Recovery Key
- [ ] Go to `/settings`
- [ ] Check "Recovery Key" section shows status
- [ ] Generate recovery key (requires password)
- [ ] Verify recovery key displayed once
- [ ] Save recovery key
- [ ] Verify status changes to "configured"

---

## User Management (Admin)

### User List
- [ ] Navigate to `/admin/users`
- [ ] Verify all users displayed with avatar, name, admin badge
- [ ] Verify user creation date shown

### Create User
- [ ] Click "Add User"
- [ ] Fill username, display name, password
- [ ] Check "Grant admin rights"
- [ ] Submit form
- [ ] Verify user appears in list with admin badge

### Edit User Permissions
- [ ] Find non-admin user
- [ ] Click "Make Admin"
- [ ] Verify admin badge appears
- [ ] Click "Revoke Admin"
- [ ] Verify admin badge removed

### Delete User
- [ ] Create test user
- [ ] Click "Delete"
- [ ] Confirm deletion
- [ ] Verify user removed from list
- [ ] Verify cannot delete yourself

---

## Gallery Navigation

### Sidebar
- [ ] Folder tree loads correctly
- [ ] Click folder loads correct content
- [ ] Current folder highlighted in sidebar
- [ ] Expand/collapse folders works
- [ ] Folder state persists after reload

### Navigation
- [ ] Browser back/forward buttons work
- [ ] URL updates correctly on navigation
- [ ] Direct URL access to folder works
- [ ] Breadcrumb navigation works

### Sorting
- [ ] Sort button changes order (uploaded/taken)
- [ ] Sort preference persists after page reload
- [ ] Sort indicator shows current sort

---

## Folder Management

### Create Folder
- [ ] Click "+" next to Folders
- [ ] Enter folder name
- [ ] Verify folder appears in sidebar
- [ ] Verify folder clickable

### Rename Folder
- [ ] Right-click folder → Rename
- [ ] Change name
- [ ] Verify name updated in sidebar

### Move Folder
- [ ] Drag folder to another folder
- [ ] Verify folder moved
- [ ] Verify contents still accessible

### Delete Folder
- [ ] Create test folder
- [ ] Right-click → Delete
- [ ] Confirm deletion
- [ ] Verify folder removed from sidebar
- [ ] Verify folder contents deleted

---

## Upload

### Single File Upload
- [ ] Click upload button opens modal
- [ ] Select single image file
- [ ] Progress bar shows during upload
- [ ] Thumbnail generated after upload
- [ ] Item appears in gallery immediately

### Multiple File Upload
- [ ] Select multiple files
- [ ] All files upload with progress
- [ ] Thumbnails generated for all

### Drag & Drop
- [ ] Drag files to gallery area
- [ ] Drop zone highlighted
- [ ] Files upload after drop

### Folder Upload
- [ ] Switch to "Folder" tab
- [ ] Select folder
- [ ] Folder structure preserved
- [ ] All files uploaded

### Upload to Safe
- [ ] Unlock safe
- [ ] Upload files to safe folder
- [ ] Verify encrypted indicator
- [ ] Verify files accessible after unlock

---

## Media Display

### Gallery Grid
- [ ] Items arranged in masonry layout
- [ ] No jumping when images load
- [ ] Resize window rearranges items
- [ ] Scroll loads more items (if applicable)

### Thumbnails
- [ ] Thumbnails load progressively
- [ ] Placeholder shown while loading
- [ ] Correct aspect ratio maintained
- [ ] Video thumbnails show play icon

### Selection
- [ ] Select all checkbox works
- [ ] Delete selected works
- [ ] Move selected works

---

## Lightbox

### Opening
- [ ] Click photo opens lightbox
- [ ] Thumbnail shown immediately
- [ ] Full image loads in background
- [ ] URL updates with photo_id
- [ ] Page reload with photo_id opens that photo

### Navigation
- [ ] Arrow keys navigate between photos
- [ ] Click arrows navigates
- [ ] Swipe on mobile navigates
- [ ] Loop at end/start (if enabled)

### Controls
- [ ] Close button works
- [ ] Escape key closes
- [ ] Close removes photo_id from URL
- [ ] Info panel shows metadata

### Video Playback
- [ ] Video plays in lightbox
- [ ] Controls visible
- [ ] Fullscreen works
- [ ] Poster image shown before play

---

## Albums

### Album View
- [ ] Click album opens lightbox
- [ ] Album photos displayed
- [ ] Album indicator bars work
- [ ] Navigation within album works

### Edit Album
- [ ] Open album editor
- [ ] Drag to reorder photos
- [ ] Remove photos from album
- [ ] Add more photos
- [ ] Set cover photo
- [ ] Save changes

### Delete Album
- [ ] Delete album (photos deleted with album)
- [ ] Verify album removed
- [ ] Verify photos removed from folder

---

## Tags & Search (Hierarchical v2)

### Tag Editor
- [ ] Open tag editor from lightbox
- [ ] "Your Tags" section shows explicit tags
- [ ] "Inherited Tags" section shows auto-calculated ancestors
- [ ] Add tag via search
- [ ] Add tag via tree browser
- [ ] Remove explicit tag (inherited recalculates)
- [ ] Cannot remove inherited tags directly

### Hierarchical Tags
- [ ] Adding "silver_fox" auto-adds "fox", "mammal", "animal"
- [ ] Removing "silver_fox" removes its ancestors (if not needed by others)
- [ ] Tag tree shows categories: Subject, Style, Environment, etc.
- [ ] Navigate tree: animal → mammal → fox → silver_fox

### Tag Search
- [ ] Search "fox night" finds items with both tags
- [ ] Search "fox -wolf" excludes wolf items
- [ ] Results show item thumbnails
- [ ] Clear search returns to folder view

### Global Search
- [ ] Type in search box
- [ ] Suggestions appear
- [ ] Click suggestion navigates
- [ ] Enter searches

---

## Safes (Encrypted Vaults)

### Create Safe
- [ ] Click "+" next to Safes
- [ ] Enter safe name
- [ ] Choose password unlock method
- [ ] Set strong password
- [ ] Verify safe created

### Create Safe with Hardware Key
- [ ] Click "+" next to Safes
- [ ] Choose hardware key method
- [ ] Register hardware key for safe
- [ ] Verify safe created

### Unlock Safe
- [ ] Click locked safe
- [ ] Enter password
- [ ] Verify unlock successful
- [ ] Contents visible

### Lock Safe
- [ ] Click lock button
- [ ] Verify safe locked
- [ ] Contents hidden/encrypted

### Safe Operations
- [ ] Create folder inside safe
- [ ] Upload files to safe
- [ ] Verify files encrypted (E2E)
- [ ] Verify thumbnails generated
- [ ] View files in lightbox
- [ ] Download files (decrypted client-side)

### Safe Sharing (Negative Test)
- [ ] Verify safe folders don't appear in share dialog
- [ ] Verify safe contents not accessible to other users

---

## Hardware Keys (WebAuthn)

### Register Key
- [ ] Go to `/settings`
- [ ] Enter key name
- [ ] Click "Add Key"
- [ ] Touch hardware key
- [ ] Verify key registered
- [ ] Verify key appears in list

### Rename Key
- [ ] Click rename icon
- [ ] Enter new name
- [ ] Save
- [ ] Verify name updated

### Delete Key
- [ ] Click delete on key
- [ ] Confirm deletion
- [ ] Verify key removed

### Login with Hardware Key
- [ ] Logout
- [ ] Enter username
- [ ] Click "Sign in with Hardware Key"
- [ ] Touch key
- [ ] Verify login successful

---

## Sharing & Permissions

### Share Folder
- [ ] Right-click folder → Share
- [ ] Search for user
- [ ] Select user
- [ ] Choose permission (Viewer/Editor)
- [ ] Add permission
- [ ] Verify user in shared list

### Access Shared Folder
- [ ] Login as shared user
- [ ] Verify folder appears in sidebar
- [ ] Verify can view contents (Viewer)
- [ ] Verify can upload (Editor)
- [ ] Verify cannot share further

### Revoke Access
- [ ] As owner, open share dialog
- [ ] Remove user from shared list
- [ ] Verify user loses access

---

## Backup & Maintenance (Admin)

### Create Backup
- [ ] Navigate to `/admin/backups`
- [ ] Click "Create Backup"
- [ ] Verify backup created
- [ ] Verify backup appears in list

### Create Full Backup
- [ ] Click "Create Full Backup"
- [ ] Wait for completion
- [ ] Verify backup file created
- [ ] Verify size reasonable

### Verify Backup
- [ ] Click "Verify" on backup
- [ ] Wait for verification
- [ ] Verify success message

### Download Backup
- [ ] Click "Download" on backup
- [ ] Verify file downloads
- [ ] Verify file not corrupted

### Restore Backup
- [ ] Click "Restore" on backup
- [ ] Confirm restore
- [ ] Verify restore successful
- [ ] Verify data restored

### Maintenance Tasks
- [ ] Navigate to `/admin/maintenance`
- [ ] View thumbnail stats
- [ ] Run "Cleanup Orphaned Thumbnails"
- [ ] Run "Regenerate Missing Thumbnails"
- [ ] Verify operations complete

---

## Error Handling & Edge Cases

### Network Errors
- [ ] Disconnect during upload
- [ ] Verify error message
- [ ] Verify can retry

### Invalid Operations
- [ ] Try to delete root folder
- [ ] Try to move folder into itself
- [ ] Try to access other user's private folder
- [ ] Verify appropriate error messages

### Large Files
- [ ] Upload large image (>10MB)
- [ ] Upload large video (>100MB)
- [ ] Verify progress shown
- [ ] Verify completes successfully

### Many Items
- [ ] Folder with 100+ photos
- [ ] Verify performance acceptable
- [ ] Scroll performance good

---

## Responsive Design

### Mobile
- [ ] Test on mobile device or emulator
- [ ] Sidebar toggle works
- [ ] Touch gestures work (swipe, tap)
- [ ] Upload works from mobile
- [ ] Lightbox fullscreen works

### Tablet
- [ ] Test on tablet
- [ ] Layout adapts correctly
- [ ] Touch interactions work

### Desktop
- [ ] Test on various window sizes
- [ ] Resize browser, layout adapts
- [ ] Keyboard shortcuts work

---

## Security Tests

### CSRF Protection
- [ ] Try POST without CSRF token
- [ ] Verify blocked

### Session Security
- [ ] Copy session cookie
- [ ] Try to use from different browser
- [ ] Verify rejected or behavior appropriate

### Safe Encryption
- [ ] Verify encrypted files on disk
- [ ] Verify cannot read without unlock
- [ ] Verify server cannot decrypt E2E files

---

## Performance

### Load Time
- [ ] Gallery loads in < 2 seconds
- [ ] Sidebar renders immediately
- [ ] Thumbnails load progressively

### Operations
- [ ] Upload 10 files simultaneously
- [ ] Delete 50 items
- [ ] Move folder with many items
- [ ] Verify responsive during operations

---

## Accessibility

### Keyboard Navigation
- [ ] Tab through all interactive elements
- [ ] Enter activates buttons/links
- [ ] Space toggles checkboxes
- [ ] Escape closes modals

### Screen Reader
- [ ] Test with screen reader
- [ ] Images have alt text
- [ ] Buttons have labels
- [ ] Modals announced correctly

### Visual
- [ ] High contrast mode
- [ ] Zoom to 200%
- [ ] Verify readable

---

## Notes

### Before Release
- Run all tests on clean database
- Run all tests on production-like data
- Test upgrade path from previous version
- Verify all external dependencies (if any)

### Browser Compatibility
- Chrome/Edge (latest)
- Firefox (latest)
- Safari (latest)
- Mobile browsers (iOS Safari, Chrome Mobile)

### Known Limitations
- WebAuthn requires HTTPS or localhost
- Safari may have limitations with some features
- Large video files may timeout on slow connections
