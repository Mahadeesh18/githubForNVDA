# GitHub for NVDA

GitHub for NVDA is an accessible add-on designed for screen reader users who rely on NVDA. It seamlessly integrates GitHub into standard Windows controls, removing the dependency on the web interface.

## Compatibility
- Requires NVDA 2024.1 or later.
- Tested up to 2026.2 (64-bit NVDA fully supported).
- License: GPL v2.

## Table of Contents
- [Getting Started](#getting-started)
- [Moving Around & Core Shortcuts](#moving-around--core-shortcuts)
- [Key Features](#key-features)
- [Settings & Configuration](#settings--configuration)
- [Developer Information](#developer-information)

## Getting Started

### 1. Assign Shortcuts
The add-on does not assign default shortcuts. You need to configure them manually:
- Open the NVDA menu > **Preferences** > **Input gestures**.
- Navigate to the _GitHub for NVDA_ section and assign a key to **"Opens the GitHub for NVDA main menu"**.

### 2. Configure Your Personal Access Token
To use the features, authenticate using a GitHub Personal Access Token (classic):
- Go to `github.com/settings/tokens`.
- Generate a classic token with the required scopes (e.g., `repo`, `notifications`, `gist`, `workflow`).
- Open NVDA menu > **Preferences** > **Settings** > *GitHub for NVDA*.
- Paste your token in **"Paste your personal access token"**, test it, and save.

## Moving Around & Core Shortcuts

Navigate using standard Windows and NVDA controls:
- **Escape:** Closes any open window.
- **Tab:** Moves between controls.
- **Control + Enter:** Saves forms containing multi-line boxes.
- **Enter:** Performs the primary action on a selected list item.
- **Applications Key:** Opens a comprehensive context menu of all available actions for the selected item.
- **Control + F:** Navigates to filter/search boxes.

## Key Features

- **My Work:** Tracks assigned tasks, pull requests requiring review, mentions, and your open issues.
- **My Repositories:** Browse, sort, and manage your repositories. Use the Applications key for advanced options like cloning, starring, or deleting (requires admin access).
- **Repository Browser:** Navigate files and folders smoothly. Edit UTF-8 text files directly or upload files/entire folders with ease.
- **Issues & Pull Requests:** View descriptions, track discussions, add comments, approve PRs, merge changes, and manage labels/assignees.
- **GitHub Actions:** Monitor workflow runs in real-time and inspect job logs.
- **Profile Management:** Update profile details, view contribution graphs, and manage organization memberships.

## Settings & Configuration

Access the add-on settings via NVDA menu > **Preferences** > **Settings** > *GitHub for NVDA*. Configure features like:
- Notification check frequency (background monitoring).
- Background announcement filters.
- File-viewing display options (word wrapping).
- Designated download folders.

## Developer Information

- **Author:** Mahadeesh
- **Email:** Mahadeesh18@gmail.com
- **GitHub Profile:** https://github.com/Mahadeesh18
- **Source Code & Contributions:** https://github.com/Mahadeesh18/githubForNVDA
