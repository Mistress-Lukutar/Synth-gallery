#!/usr/bin/env python3
"""Add deprecation warnings to all proxy functions in database.py"""
import re

# Read the file
with open('app/database.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern to match deprecated functions
# Matches: def func_name(...):\n    """...\n    \n    DEPRECATED: ...\n    """\n    return ...
# Or: def func_name(...):\n    """...\n    \n    DEPRECATED: ...\n    """\n    _get_...

# We'll add warnings.warn() after the docstring and before the return/statement

def add_warning_to_function(content, func_name, warning_msg):
    """Add deprecation warning to a specific function."""
    # Pattern to find the function and add warning after docstring
    pattern = rf'(def {func_name}\([^)]*\):\s+"""[^"]*DEPRECATED[^"]*"""\s*)'
    replacement = rf'\1    warnings.warn("{warning_msg}", DeprecationWarning, stacklevel=2)\n'
    return re.sub(pattern, replacement, content, flags=re.DOTALL)

# List of all deprecated functions with their warning messages
deprecated_functions = [
    # User functions
    ('create_user', 'create_user() is deprecated, use UserRepository.create()'),
    ('get_user_by_username', 'get_user_by_username() is deprecated, use UserRepository.get_by_username()'),
    ('get_user_by_id', 'get_user_by_id() is deprecated, use UserRepository.get_by_id()'),
    ('is_user_admin', 'is_user_admin() is deprecated, use UserRepository.is_admin()'),
    ('set_user_admin', 'set_user_admin() is deprecated, use UserRepository.set_admin()'),
    ('search_users', 'search_users() is deprecated, use UserRepository.search()'),
    ('update_user_password', 'update_user_password() is deprecated, use UserRepository.update_password()'),
    ('update_user_display_name', 'update_user_display_name() is deprecated, use UserRepository.update_display_name()'),
    ('delete_user', 'delete_user() is deprecated, use UserRepository.delete()'),
    ('list_users', 'list_users() is deprecated, use UserRepository.list_all()'),
    ('authenticate_user', 'authenticate_user() is deprecated, use UserRepository.authenticate()'),
    # Session functions
    ('create_session', 'create_session() is deprecated, use SessionRepository.create()'),
    ('get_session', 'get_session() is deprecated, use SessionRepository.get_valid()'),
    ('delete_session', 'delete_session() is deprecated, use SessionRepository.delete()'),
    ('cleanup_expired_sessions', 'cleanup_expired_sessions() is deprecated, use SessionRepository.cleanup_expired()'),
    # Folder functions
    ('create_folder', 'create_folder() is deprecated, use FolderRepository.create()'),
    ('get_folder', 'get_folder() is deprecated, use FolderRepository.get_by_id()'),
    ('update_folder', 'update_folder() is deprecated, use FolderRepository.update()'),
    ('delete_folder', 'delete_folder() is deprecated, use FolderRepository.delete()'),
    ('get_user_folders', 'get_user_folders() is deprecated, use FolderRepository.list_by_user()'),
    ('get_folder_children', 'get_folder_children() is deprecated, use FolderRepository.get_children()'),
    ('get_folder_breadcrumbs', 'get_folder_breadcrumbs() is deprecated, use FolderRepository.get_breadcrumbs()'),
    # Permission functions
    ('add_folder_permission', 'add_folder_permission() is deprecated, use PermissionRepository.grant()'),
    ('remove_folder_permission', 'remove_folder_permission() is deprecated, use PermissionRepository.revoke()'),
    ('update_folder_permission', 'update_folder_permission() is deprecated, use PermissionRepository.update_permission()'),
    ('get_folder_permissions', 'get_folder_permissions() is deprecated, use PermissionRepository.list_permissions()'),
    ('get_user_permission', 'get_user_permission() is deprecated, use PermissionRepository.get_permission()'),
    ('can_view_folder', 'can_view_folder() is deprecated, use PermissionRepository.can_view()'),
    ('can_edit_folder', 'can_edit_folder() is deprecated, use PermissionRepository.can_edit()'),
    ('can_access_folder', 'can_access_folder() is deprecated, use PermissionRepository.can_view()'),
    # Photo functions
    ('mark_photo_encrypted', 'mark_photo_encrypted() is deprecated, use PhotoRepository.mark_encrypted()'),
    ('mark_photo_decrypted', 'mark_photo_decrypted() is deprecated, use PhotoRepository.mark_encrypted()'),
    ('get_user_unencrypted_photos', 'get_user_unencrypted_photos() is deprecated, use PhotoRepository with custom query'),
    ('get_photo_by_id', 'get_photo_by_id() is deprecated, use PhotoRepository.get_by_id()'),
    ('get_photo_owner_id', 'get_photo_owner_id() is deprecated, use PhotoRepository.get_by_id()'),
    ('update_photo_thumbnail_dimensions', 'update_photo_thumbnail_dimensions() is deprecated, use PhotoRepository.update_thumbnail_dimensions()'),
    # Safe functions
    ('create_safe', 'create_safe() is deprecated, use SafeRepository.create()'),
    ('get_safe', 'get_safe() is deprecated, use SafeRepository.get_by_id()'),
    ('get_user_safes', 'get_user_safes() is deprecated, use SafeRepository.list_by_user()'),
    ('update_safe', 'update_safe() is deprecated, use SafeRepository.update()'),
    ('delete_safe', 'delete_safe() is deprecated, use SafeRepository.delete()'),
    ('get_safe_by_folder_id', 'get_safe_by_folder_id() is deprecated, use SafeRepository.get_by_folder()'),
    ('create_safe_session', 'create_safe_session() is deprecated, use SafeRepository.create_session()'),
    ('get_safe_session', 'get_safe_session() is deprecated, use SafeRepository.get_session()'),
    ('delete_safe_session', 'delete_safe_session() is deprecated, use SafeRepository.delete_session()'),
    ('cleanup_expired_safe_sessions', 'cleanup_expired_safe_sessions() is deprecated, use SafeRepository.cleanup_expired_sessions()'),
    ('get_user_unlocked_safes', 'get_user_unlocked_safes() is deprecated, use SafeRepository.list_unlocked()'),
    ('is_safe_unlocked_for_user', 'is_safe_unlocked_for_user() is deprecated, use SafeRepository.is_unlocked()'),
]

# Read the file again
with open('app/database.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Process each function
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    new_lines.append(line)
    
    # Check if this is a def line
    if line.startswith('def '):
        func_name = line.split('(')[0].replace('def ', '').strip()
        
        # Check if this function is in our deprecated list
        matching_deprecations = [(name, msg) for name, msg in deprecated_functions if name == func_name]
        
        if matching_deprecations:
            # Find the closing """ of the docstring
            j = i + 1
            in_docstring = False
            docstring_end = -1
            
            while j < len(lines):
                if '"""' in lines[j]:
                    if not in_docstring:
                        in_docstring = True
                    else:
                        docstring_end = j
                        break
                j += 1
            
            # If we found the docstring end, insert warning after it
            if docstring_end != -1:
                # Add all lines up to and including docstring
                for k in range(i + 1, docstring_end + 1):
                    new_lines.append(lines[k])
                
                # Add the warning
                func_name_dep = matching_deprecations[0][0]
                warning_msg = matching_deprecations[0][1]
                indent = '    '  # 4 spaces
                new_lines.append(f'{indent}warnings.warn("{warning_msg}", DeprecationWarning, stacklevel=2)\n')
                
                # Skip the lines we've already added
                i = docstring_end
    
    i += 1

# Write the file
with open('app/database.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Deprecation warnings added successfully!")
