import pandas as pd
import sys
import gams.transfer as gt

def data_in(file_path,parsed_args):
    # Read in solar and wind intermediate input data
    data_solar = pd.read_excel(file_path,sheet_name="Solar Baltic")
    data_wind = pd.read_excel(file_path,sheet_name="Wind Balitc")
    data_wind = data_wind.rename(columns={"7 lva": "lva", "8 ltu": "ltu", "9 est": "est"})
    data_factor = {
    "wind": pd.read_excel(file_path, sheet_name="Primary factors", skiprows=9, nrows=6, usecols="A:P"),
    "solar": pd.read_excel(file_path, sheet_name="Primary factors", skiprows=1, nrows=6, usecols="A:P")
}
    # Define columns to keep for each dataset. This includes sector list.
    columns_to_keep = ["VDFB"] + parsed_args.columns_to_keep_wind.split()
    data_wind = data_wind[columns_to_keep]
    data_wind.loc[data_wind['VDFB'] == 'Total', 'VDFB'] = "Total Intermediate"
    columns_to_keep = ["VDFB"] + parsed_args.columns_to_keep_solar.split()
    data_solar = data_solar[columns_to_keep]
    data_solar.loc[data_solar['VDFB'] == 'Total', 'VDFB'] = "Total Intermediate"
    columns_to_keep = ["Wind Power"] + parsed_args.columns_to_keep_factor.split()
    data_factor["wind"] = data_factor["wind"][columns_to_keep]
    data_factor["wind"] = data_factor["wind"].rename(columns={"Wind Power": "VDFB",
                                                              **dict(zip(parsed_args.columns_to_keep_factor.split(), parsed_args.columns_to_keep_wind.split()))})
    data_factor["wind"].loc[data_factor["wind"]['VDFB'] == 'Total', 'VDFB'] = "Total Factor"
    data_wind = pd.concat(
        [data_wind, data_factor["wind"]],
        axis=0,
        ignore_index=True
    )
    columns_to_keep = ["Solar Power"] + parsed_args.columns_to_keep_factor.split()
    data_factor["solar"] = data_factor["solar"][columns_to_keep]
    data_factor["solar"] = data_factor["solar"].rename(columns={"Solar Power": "VDFB",
                                                              **dict(zip(parsed_args.columns_to_keep_factor.split(), parsed_args.columns_to_keep_solar.split()))})
    data_factor["solar"].loc[data_factor["solar"]['VDFB'] == 'Total', 'VDFB'] = "Total Factor"
    
    data_solar = pd.concat(
        [data_solar, data_factor["solar"]],
        axis=0,
        ignore_index=True
    )
    data = {
        "wind": data_wind,
        "solar": data_solar
    }
    return data

def shares(data, parsed_args):
    factor_inputs = [
        "1 Land",
        "2 UnSkLab",
        "3 SkLab",
        "4 Capital",
        "5 NatRes",
    ]
    df_shares = {}
    for technology, frame in data.items():
        values = frame.set_index("VDFB")
        for total in ["Total Intermediate", "Total Factor"]:
            if (values.index == total).sum() != 1:
                raise ValueError(f"{technology}: expected exactly one '{total}' row")
        result = values.drop(index=["Total Intermediate", "Total Factor"]).copy()
        factor_rows = result.index.isin(factor_inputs)
        # Recompute denominators because the workbook totals are rounded independently.
        for total, rows in [("Total Intermediate", ~factor_rows), ("Total Factor", factor_rows)]:
            totals = result.loc[rows].sum(axis=0, skipna=False)
            if totals.isna().any() or (totals == 0).any():
                raise ValueError(f"{technology}: computed '{total}' must be non-missing and nonzero")
            result.loc[rows] = result.loc[rows].div(totals, axis="columns")
        df_shares[technology] = result.reset_index()
    return df_shares
    
def main(args):
    parsed_args = parse_args(args)
    file_path = parsed_args.shares_database
    data = data_in(file_path,parsed_args)
    result = shares(data, parsed_args)
    with pd.ExcelWriter("input_shares.xlsx") as writer:
        for sheet_name, df in result.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    
    return result

if __name__ == "__main__":
    from shares import parse_args
    main(sys.argv[1:])