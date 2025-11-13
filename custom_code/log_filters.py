"""
Custom logging filters for SNEx2.
"""
import logging


class CompactDashSessionErrors(logging.Filter):
    """
    Filter to compact django_plotly_dash session KeyError logs to a single line.
    
    These errors occur when a Dash app session expires, but are handled
    gracefully by the frontend JavaScript which automatically reloads
    the component with a fresh session. Instead of a multi-line traceback,
    we log a single informative line with the key details.
    """
    
    def filter(self, record):
        # Only process ERROR level logs
        if record.levelname != 'ERROR':
            return True
        
        # Check if this involves django_plotly_dash layout requests
        message = record.getMessage()
        
        if '/django_plotly_dash/app/' in message and '_dash-layout' in message:
            # Check if there's a KeyError for dpd-initial-args
            if record.exc_info and len(record.exc_info) > 0:
                exc_type, exc_value, exc_traceback = record.exc_info
                
                if exc_type is KeyError:
                    key_str = str(exc_value).strip("'\"")
                    
                    if key_str.startswith('dpd-initial-args-'):
                        # Extract app name from the URL path
                        try:
                            app_name = message.split('/app/')[1].split('/')[0]
                        except:
                            app_name = 'Unknown'
                        
                        # Create a single-line informative message
                        compact_message = (
                            f"Dash session KeyError for app '{app_name}' - "
                            f"session key: {key_str} "
                            f"(frontend will auto-reload with fresh session)"
                        )
                        
                        # Replace the record's message and remove traceback
                        record.msg = compact_message
                        record.args = ()
                        record.exc_info = None
                        record.exc_text = None
                        
                        return True  # Log the modified record
        
        # Allow all other logs unchanged
        return True
