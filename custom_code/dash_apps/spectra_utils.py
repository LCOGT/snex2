"""Shared utilities for spectra plotting Dash apps."""

# Element definitions shared between spectra.py and spectra_individual.py
elements = {
    'H': {'color': '#ff0000', 'waves': [3970, 4102, 4341, 4861, 6563]},
    'He': {'color': '#002157', 'waves': [4472, 5876, 6678, 7065]},
    'He II': {'color': '#003b99', 'waves': [3203, 4686]},
    'O': {'color': '#007236', 'waves': [7774, 7775, 8447, 9266]},
    'O II': {'color': '#00a64d', 'waves': [3727]},
    'O III': {'color': '#00bf59', 'waves': [4959, 5007]},
    'Na': {'color': '#aba000', 'waves': [5890, 5896, 8183, 8195]},
    'Mg': {'color': '#8c6239', 'waves': [2780, 2852, 3829, 3832, 3838, 4571, 5167, 5173, 5184]},
    'Mg II': {'color': '#bf874e', 'waves': [2791, 2796, 2803, 4481]},
    'Si II': {'color': '#5674b9', 'waves': [3856, 5041, 5056, 5670, 6347, 6371]},
    'S II': {'color': '#a38409', 'waves': [5433, 5454, 5606, 5640, 5647, 6715]},
    'Ca II': {'color': '#005050', 'waves': [3934, 3969, 7292, 7324, 8498, 8542, 8662]},
    'Fe II': {'color': '#f26c4f', 'waves': [5018, 5169]},
    'Fe III': {'color': '#f9917b', 'waves': [4397, 4421, 4432, 5129, 5158]},
    'C II': {'color': '#303030', 'waves': [4267, 4745, 6580, 7234]},
    'Galaxy': {'color': '#000000', 'waves': [4341, 4861, 6563, 6548, 6583, 6300, 3727, 4959, 5007, 2798, 6717, 6731]},
    'Tellurics': {'color': '#b7b7b7', 'waves': [6867, 6884, 7594, 7621]},
    'Flash CNO': {'color': '#0064c8', 'waves': [4648, 5696, 5801, 4640, 4058, 4537, 5047, 7109, 7123, 4604, 4946, 3410, 5597, 3811, 3835]},
    'SN Ia': {'color': '#ff9500', 'waves': [3856, 5041, 5056, 5670, 6347, 6371, 5433, 5454, 5606, 5640, 5647, 6715, 3934, 3969, 7292, 7324, 8498, 8542, 8662]},
}


def calculate_flux_range(graph_data, min_flux=0, max_flux=0):
    """
    Calculate the actual min/max flux from spectrum data, excluding element lines.
    
    Args:
        graph_data: Dict containing 'data' key with list of trace objects
        min_flux: Initial minimum flux value (default 0)
        max_flux: Initial maximum flux value (default 0)
    
    Returns:
        Tuple of (actual_min_flux, actual_max_flux)
    """
    actual_min_flux = min_flux
    actual_max_flux = max_flux
    for trace in graph_data['data']:
        if trace['name'] not in elements and 'y' in trace and trace['y']:
            y_vals = [v for v in trace['y'] if v is not None]
            if y_vals:
                trace_min = min(y_vals)
                trace_max = max(y_vals)
                if actual_min_flux == 0 and actual_max_flux == 0:
                    actual_min_flux = trace_min
                    actual_max_flux = trace_max
                else:
                    actual_min_flux = min(actual_min_flux, trace_min)
                    actual_max_flux = max(actual_max_flux, trace_max)
    return actual_min_flux, actual_max_flux

