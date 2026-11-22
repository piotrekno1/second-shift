# Review a deployed test copy

The app runs somewhere else, such as a staging server. A browser server drives
it, and you reach that browser only through the approved browser tools.

## The address

- Use the address from the request. For a scheduled run without one, use the
  test address the app notes list.
- Use an address only when the app notes list its host as a test address. If
  the request names another host, or the notes list none, stop and say so.
  Never review production.
- Stay on that host. The browser server may also block other sites.

## Tools

| Action | Tool |
| --- | --- |
| open | `browser_navigate` |
| back | `browser_navigate_back` |
| look | `browser_snapshot` |
| click | `browser_click` |
| type | `browser_type` |
| press | `browser_press_key` |
| resize | `browser_resize` |
| screenshot | `browser_take_screenshot` |
| console | `browser_console_messages` |
| network | `browser_network_requests` |

Read the page with `browser_snapshot` after you open, click or type. Some
servers save the snapshot that comes with an action to a file on the server,
which you cannot read.

Never close the browser. Closing it may lose the saved sign-in.

## Before each user

1. If the run has a tool that resets the app's test data, call it. The run
   prompt says when that optional tool is missing. Without it, say in the
   report that the app was not reset between users.
2. Open the first page and check that the browser is signed in. Use the sign
   the app notes give, or a sign like an account menu. If you land on a sign-in
   page, stop: the saved sign-in has expired, and the owner must renew it.
3. Resize the window back to 1280 by 800, except for the phone user.

## Limits of this target

- Every user shares one signed-in browser. A real new user starts signed out
  and signs up first. Walk the sign-up page up to the password field, then go
  back without submitting. Say in the report that sign-up was not completed.
- Clicks and typing change real data in the test copy. Do not delete, send,
  pay, publish, invite real people, or confirm anything that cannot be undone.
  Stop one step before such an action and report what it would have done.
- Each tool call has a short time limit. If a page is slow and a call times
  out, try it once more. If it fails again, list it under flaky results.
- Screenshots stay on the browser server. Report their file names.
