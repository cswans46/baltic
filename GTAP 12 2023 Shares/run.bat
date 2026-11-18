@echo Produce GTAP 2023 Shares
@ECHO on
goto application
@ECHO ======= Running shares.py ============
call python shares.py --database "../GTAP 2023 Baltic countries RE sector input cost structure.xlsx"
@ECHO ======= Running shares_gams.py ============
call python shares_gams.py --shares_database "..\GDX12aPower23\GSDFDAT.GDX"
@ECHO ======= Finished running shares_gams.py ============

:application
call python application.py