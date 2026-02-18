#!/usr/bin/env python3
"""Add deprecation warnings to ALL proxy functions in database.py"""

# Mapping of function names to their recommended replacements
DEPRECATION_MAP = {
    # User functions
    'create_user': 'UserRepository.create()',
    'get_user_by_username': 'UserRepository.get_by_username()',
    'get_user_by_id': 'UserRepository.get_by_id()',
    'is_user_admin': 'UserRepository.is_admin()',
    'set_user_admin': 'UserRepository.set_admin()',
    'search_users': 'UserRepository.search()',
    'update_user_password': 'UserRepository.update_password()',
    'update_user_display_name': 'UserRepository.update_display_name()',
    'delete_user': 'UserRepository.delete()',
    'list_users': 'UserRepository.list_all()',
    'authenticate_user': 'UserRepository.authenticate()',
    # Session functions
    'create_session': 'SessionRepository.create()',
    'get_session': 'SessionRepository.get_valid()',
    'delete_session': 'SessionRepository.delete()',
    'cleanup_expired_sessions': 'SessionRepository.cleanup_expired()',
    # Folder functions
    'create_folder': 'FolderRepository.create()',
    'get_folder': 'FolderRepository.get_by_id()',
    'update_folder': 'FolderRepository.update()',
    'delete_folder': 'FolderRepository.delete()',
    'get_user_folders': 'FolderRepository.list_by_user()',
    'get_folder_children': 'FolderRepository.get_children()',
    'get_folder_breadcrumbs': 'FolderRepository.get_breadcrumbs()',
    'get_folder_tree': 'FolderService.get_folder_tree()',
    'get_folder_contents': 'FolderService.get_folder_contents()',
    'create_default_folder': 'UserSettingsService.get_default_folder()',
    # Permission functions
    'add_folder_permission': 'PermissionRepository.grant()',
    'remove_folder_permission': 'PermissionRepository.revoke()',
    'update_folder_permission': 'PermissionRepository.update_permission()',
    'get_folder_permissions': 'PermissionRepository.list_permissions()',
    'get_user_permission': 'PermissionRepository.get_permission()',
    'can_view_folder': 'PermissionRepository.can_view()',
    'can_edit_folder': 'PermissionRepository.can_edit()',
    'can_access_folder': 'PermissionRepository.can_view()',
    'can_access_photo': 'PermissionService.can_access_photo()',
    'can_delete_photo': 'PermissionService.can_delete_photo()',
    'can_access_album': 'PermissionService.can_access_album()',
    'can_delete_album': 'PermissionService.can_delete_album()',
    'can_edit_album': 'PermissionService.can_edit_album()',
    # Photo functions
    'mark_photo_encrypted': 'PhotoRepository.mark_encrypted()',
    'mark_photo_decrypted': 'PhotoRepository.mark_encrypted()',
    'get_user_unencrypted_photos': 'PhotoRepository with custom query',
    'get_photo_by_id': 'PhotoRepository.get_by_id()',
    'get_photo_owner_id': 'PhotoRepository.get_by_id()',
    'update_photo_thumbnail_dimensions': 'PhotoRepository.update_thumbnail_dimensions()',
    'add_photos_to_album': 'PhotoService.add_photos_to_album()',
    'remove_photos_from_album': 'PhotoService.remove_photos_from_album()',
    'reorder_album_photos': 'PhotoService.reorder_album_photos()',
    'set_album_cover': 'PhotoService.set_album_cover()',
    'get_album': 'PhotoRepository.get_album()',
    'get_album_photos': 'PhotoRepository.get_album_photos()',
    'get_available_photos_for_album': 'PhotoRepository.get_available_for_album()',
    # Safe functions
    'create_safe': 'SafeRepository.create()',
    'get_safe': 'SafeRepository.get_by_id()',
    'get_user_safes': 'SafeRepository.list_by_user()',
    'update_safe': 'SafeRepository.update()',
    'delete_safe': 'SafeRepository.delete()',
    'get_safe_by_folder_id': 'SafeRepository.get_by_folder()',
    'is_folder_in_safe': 'SafeRepository.get_by_folder()',
    'get_folder_safe_id': 'SafeRepository.get_by_folder()',
    'create_safe_session': 'SafeRepository.create_session()',
    'get_safe_session': 'SafeRepository.get_session()',
    'delete_safe_session': 'SafeRepository.delete_session()',
    'cleanup_expired_safe_sessions': 'SafeRepository.cleanup_expired_sessions()',
    'get_user_unlocked_safes': 'SafeRepository.list_unlocked()',
    'is_safe_unlocked_for_user': 'SafeRepository.is_unlocked()',
    'get_safe_folders': 'SafeRepository.get_folders()',
    'get_safe_tree_for_user': 'SafeRepository.get_tree_for_user()',
    'create_folder_in_safe': 'FolderService.create_folder()',
    'move_folder_to_safe': 'SafeRepository.assign_folder()',
    # User settings functions
    'get_user_default_folder': 'UserSettingsService.get_default_folder()',
    'set_user_default_folder': 'UserSettingsService.set_default_folder()',
    'get_collapsed_folders': 'UserSettingsService.get_collapsed_folders()',
    'set_collapsed_folders': 'UserSettingsService.set_collapsed_folders()',
    'toggle_folder_collapsed': 'UserSettingsService.toggle_collapsed_folder()',
    'get_folder_sort_preference': 'UserSettingsService.get_sort_preference()',
    'set_folder_sort_preference': 'UserSettingsService.set_sort_preference()',
    'get_user_encryption_keys': 'UserSettingsService.get_encryption_keys()',
    'set_user_encryption_keys': 'UserSettingsService.set_encryption_keys()',
    'clear_recovery_key': 'UserSettingsService.clear_recovery_key()',
    'set_recovery_encrypted_dek': 'UserSettingsService.set_recovery_key()',
    'get_recovery_encrypted_dek': 'UserSettingsService.get_recovery_key()',
    # Move functions
    'move_photo_to_folder': 'PhotoService.move_photo()',
    'move_album_to_folder': 'PhotoService.move_album()',
    'move_photos_to_folder': 'PhotoService.batch_move()',
    'move_albums_to_folder': 'PhotoService.batch_move()',
}

with open('app/database.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    new_lines.append(line)
    
    # Check if this is a function definition
    if line.startswith('def '):
        func_name = line.split('(')[0].replace('def ', '').strip()
        
        if func_name in DEPRECATION_MAP:
            # Find the end of docstring
            j = i + 1
            docstring_end = -1
            in_docstring = False
            
            while j < len(lines):
                if '"""' in lines[j]:
                    if not in_docstring:
                        in_docstring = True
                    else:
                        docstring_end = j
                        break
                j += 1
            
            if docstring_end != -1:
                # Check if already has warning
                has_warning = False
                for k in range(docstring_end + 1, min(docstring_end + 5, len(lines))):
                    if 'warnings.warn' in lines[k]:
                        has_warning = True
                        break
                
                if not has_warning:
                    # Add lines up to and including docstring
                    for k in range(i + 1, docstring_end + 1):
                        new_lines.append(lines[k])
                    
                    # Add warning
                    replacement = DEPRECATION_MAP[func_name]
                    indent = '    '
                    new_lines.append(f'{indent}warnings.warn("{func_name}() is deprecated, use {replacement}", DeprecationWarning, stacklevel=2)\n')
                    
                    # Skip lines we already added
                    i = docstring_end
    
    i += 1

with open('app/database.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Deprecation warnings added to all proxy functions!")
